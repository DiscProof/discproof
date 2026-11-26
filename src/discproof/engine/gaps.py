"""Pre-gap (INDEX 00 -> 01) detection from the Q sub-channel, and gap silence.

Inside a pre-gap the Q sub-channel reports the *next* track with INDEX 00 and a
relative time counting down to its INDEX 01. Before the gap it reports the previous
track. So for track N the gap starts at the first sector whose Q says "track N".

Real drives make this messy, which the detector handles:

* Q may belong to a sector or two away from the requested one ("lag"): positions
  are taken from the absolute time *inside* each Q frame, not the requested LBA;
* about 1 in 100 Q frames carries the MCN/ISRC (ADR 2/3) instead of a position,
  and some fail their CRC: those are re-read, then a neighbour is used;
* the countdown in the first gap sector equals the gap length, a cross-check;
* the boundary is read a second time to confirm it.

The search probes 2 s, 4 s, 8 s ... before INDEX 01 (most gaps are 2 s), then
binary-searches between the last two probes: about 10 single-sector reads per track.
Results within ``TOLERANCE`` sectors of each other count as agreeing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from discproof.drive.sense import DriveError
from discproof.drive.subchannel import QPosition, parse_q
from discproof.drive.toc import Toc
from discproof.engine.pcm import peak
from discproof.engine.source import AudioSource

TOLERANCE = 2
#: Below this peak (1% of full scale, -40 dBFS) a gap is "near-silent": tape hiss,
#: room tone or dither rather than music.
QUIET_PEAK = 328
FIRST_PROBE = 150  # 2 seconds
READ_ATTEMPTS = 3
SILENCE_BATCH = 26


@dataclass(frozen=True, slots=True)
class GapResult:
    track: int
    pregap: int  # sectors of INDEX 00 before INDEX 01 (0 = no pre-gap)
    confirmed: bool  # a second reading agreed (within TOLERANCE)
    #: Peak sample level of the gap's audio, 0..32768 (None: not measured / no gap).
    peak: int | None = None

    @property
    def silent(self) -> bool | None:
        """Digital silence (None: not measured)."""
        return None if self.peak is None else self.peak == 0

    @property
    def quiet(self) -> bool | None:
        """Silent or near-silent (noise floor only, no music)."""
        return None if self.peak is None else self.peak < QUIET_PEAK


class GapDetector:
    def __init__(
        self,
        source: AudioSource,
        toc: Toc,
        *,
        check_silence: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.source = source
        self.toc = toc
        self.check_silence = check_silence
        self._progress = on_progress or (lambda done, total: None)
        self.reads = 0

    # ------------------------------------------------------------ Q access

    def _read_q(self, lba: int) -> QPosition | None:
        try:
            r = self.source.read_cd(lba, 1, subq=True)
        except DriveError:
            return None
        self.reads += 1
        if not r.subq:
            return None
        q = parse_q(r.subq[0])
        if isinstance(q, QPosition) and q.crc_ok is not False:
            return q
        return None

    def _q(self, lba: int) -> QPosition | None:
        """A trustworthy position frame at (or right next to) ``lba``."""
        for _ in range(READ_ATTEMPTS):
            q = self._read_q(lba)
            if q is not None:
                return q
        for near in (lba + 1, lba - 1, lba + 2, lba - 2):
            if 0 <= near < self.toc.leadout:
                q = self._read_q(near)
                if q is not None:
                    return q
        return None

    def _in_gap(self, lba: int, number: int) -> tuple[bool, QPosition] | None:
        """Is ``lba`` at or after the start of track ``number``'s pre-gap?"""
        q = self._q(lba)
        if q is None or q.track == 0xAA:
            return None
        return q.track >= number, q

    # ------------------------------------------------------------ search

    def detect(self, number: int) -> GapResult:
        track = self.toc.track(number)
        start = track.start
        if number == self.toc.first:
            # Track 1's pre-gap is everything before its INDEX 01 (HTOA if > 0).
            return GapResult(number, start, True, self._peak(0, start))
        prev = self.toc.track(number - 1)
        if not (track.is_audio and prev.is_audio) or track.session != prev.session:
            return GapResult(number, 0, False)
        lo_bound = prev.start + 1

        first = self._search(number, start, lo_bound)
        if first is None:
            return GapResult(number, 0, False)
        pregap, confirmed = first
        level = self._peak(start - pregap, start) if pregap else None
        return GapResult(number, pregap, confirmed, level)

    def _search(self, number: int, start: int, lo_bound: int) -> tuple[int, bool] | None:
        """(pre-gap length, confirmed), or None if Q could not be read at all."""
        probe = self._in_gap(start - 1, number)
        if probe is None:
            return None
        in_gap, q_last = probe
        if not in_gap or q_last.index != 0:
            # No pre-gap, unless Q lags by a sector or two: check a bit earlier.
            earlier = self._in_gap(max(lo_bound, start - 1 - TOLERANCE - 1), number)
            if earlier is None or not earlier[0] or earlier[1].index != 0:
                return 0, True

        # Gallop back until we are before the gap (Q says the previous track).
        step = FIRST_PROBE
        hi = start - 1  # known in gap
        lo = None  # known before gap
        while True:
            x = max(lo_bound, start - step)
            r = self._in_gap(x, number)
            if r is None:
                return None
            if not r[0]:
                lo = x
                break
            hi = x
            if x == lo_bound:
                break  # the whole previous track is "gap": accept it
            step *= 2
        if lo is None:
            return start - lo_bound, False

        # Binary search: lo before the gap, hi inside it.
        while hi - lo > 1:
            mid = (lo + hi) // 2
            r = self._in_gap(mid, number)
            if r is None:
                return None
            if r[0]:
                hi = mid
            else:
                lo = mid

        # The first gap sector by the Q frame's own clock (handles Q lag), checked
        # against the countdown, and read a second time.
        first_q = self._q(hi)
        if first_q is None or first_q.track != number:
            return start - hi, False
        by_clock = start - first_q.absolute_lba
        by_count = first_q.relative_frames if first_q.index == 0 else by_clock
        pregap = by_clock if abs(by_clock - (start - hi)) <= TOLERANCE + 2 else start - hi
        again = self._in_gap(hi, number)
        before = self._in_gap(lo, number)
        confirmed = (
            abs(by_count - pregap) <= TOLERANCE
            and again is not None
            and again[0]
            and before is not None
            and not before[0]
        )
        return max(0, pregap), confirmed

    # ------------------------------------------------------------ silence

    def _peak(self, a: int, b: int) -> int | None:
        """Peak level of the audio in [a, b), ignoring the edge sectors (whose samples
        can belong to the neighbouring track through the read offset)."""
        if not self.check_silence or b <= a:
            return None
        if b - a <= 2:
            return 0
        level = 0
        lba = a + 1
        try:
            while lba < b - 1:
                n = min(SILENCE_BATCH, b - 1 - lba)
                level = max(level, peak(self.source.read_cd(lba, n).audio))
                lba += n
        except DriveError:
            return None
        return level

    def detect_all(self, numbers: list[int] | None = None) -> dict[int, GapResult]:
        audio = [t.number for t in self.toc.audio_tracks]
        wanted = [n for n in (numbers or audio) if n in audio]
        out: dict[int, GapResult] = {}
        for i, n in enumerate(wanted):
            out[n] = self.detect(n)
            self._progress(i + 1, len(wanted))
        return out
