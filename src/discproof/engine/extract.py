"""The extraction engine: burst, secure and test-and-copy ripping.

A selection of tracks is grouped into contiguous *runs*. Each run is ripped as one
continuous stream of drive sectors, offset-corrected into true samples, and split at
the track boundaries. Pre-gaps therefore stay with the previous track (the
AccurateRip-compatible layout), and track junctions need no separate synchronisation.

Secure mode, per batch:

1. read twice with the cache defeated in between (or, on the fast path — accurate
   stream, no cache, trusted C2 — once);
2. accept sectors that match (and carry no trusted C2 flag);
3. re-read every other sector in rounds of ``reads_per_round``; accept a version that
   ``agree_threshold`` reads of one round agree on; after ``quality`` rounds keep the
   most frequent version and report the sector as suspicious.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from discproof.drive.constants import BYTES_PER_SAMPLE, SAMPLES_PER_SECTOR, SECTOR_SIZE
from discproof.drive.msf import frames_to_timestamp
from discproof.drive.toc import Toc
from discproof.engine.events import (
    BlockDone,
    Event,
    HtoaInfo,
    PassStarted,
    Progress,
    Retrying,
    RipFinished,
    RipResult,
    RipStarted,
    SectorStatus,
    SpeedChanged,
    SuspiciousPosition,
    TrackDone,
    TrackResult,
)
from discproof.engine.pcm import TrackChecksum, peak
from discproof.engine.profile import DriveProfile, ExtractionSettings, Mode
from discproof.engine.reader import Block, BoolArray, Reader
from discproof.engine.source import AudioSource

#: Receives each finished track's PCM (copy pass only): (track number, audio bytes).
TrackSink = Callable[[int, bytes], None]

_SILENT_SECTOR = bytes(SECTOR_SIZE)
_KNOWN_WINDOW = 256  # sectors of verified audio kept for jitter alignment


class _CancelledError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Span:
    number: int
    start: int  # true LBA
    end: int

    @property
    def samples(self) -> int:
        return (self.end - self.start) * SAMPLES_PER_SECTOR


@dataclass(slots=True)
class _TrackState:
    span: _Span
    checksum: TrackChecksum = field(default_factory=TrackChecksum)
    chunks: list[bytes] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)
    recovered: int = 0
    filled_samples: int = 0
    started_at: float | None = None


def track_spans(toc: Toc, settings: ExtractionSettings) -> list[_Span]:
    """The true-LBA range of every selected track; HTOA becomes track 0."""
    audio = toc.audio_tracks
    wanted = set(settings.tracks) if settings.tracks is not None else {t.number for t in audio}
    spans = [_Span(t.number, t.start, toc.end(t.number)) for t in audio if t.number in wanted]
    first = toc.tracks[0]
    if settings.rip_htoa and first.is_audio and first.number == 1 and first.start > 0:
        spans.insert(0, _Span(0, 0, first.start))
    return spans


def _runs(spans: list[_Span]) -> list[list[_Span]]:
    runs: list[list[_Span]] = []
    for span in spans:
        if runs and runs[-1][-1].end == span.start:
            runs[-1].append(span)
        else:
            runs.append([span])
    return runs


def _drive_range(run: list[_Span], offset: int) -> tuple[int, int, int]:
    """Drive sectors [d0, d1) covering the run, and the samples to skip at d0."""
    first = run[0].start * SAMPLES_PER_SECTOR + offset
    end = run[-1].end * SAMPLES_PER_SECTOR + offset
    d0 = first // SAMPLES_PER_SECTOR
    d1 = -(-end // SAMPLES_PER_SECTOR)
    return d0, d1, first - d0 * SAMPLES_PER_SECTOR


class Extractor:
    def __init__(
        self,
        source: AudioSource,
        profile: DriveProfile,
        settings: ExtractionSettings | None = None,
        *,
        toc: Toc | None = None,
        on_event: Callable[[Event], None] | None = None,
        on_track: TrackSink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.profile = profile
        self.settings = settings or ExtractionSettings()
        self.toc = toc or source.read_toc()
        self.reader = Reader(source, self.toc, profile)
        self._emit = on_event or (lambda _e: None)
        self._on_track = on_track
        self._clock = clock
        self._cancel = threading.Event()
        self._running = threading.Event()
        self._running.set()
        self._known: dict[int, bytes] = {}
        self._speed: float | None = self.settings.speed

    # ------------------------------------------------------------ control

    def cancel(self) -> None:
        self._cancel.set()
        self._running.set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def _checkpoint(self) -> None:
        self._running.wait()
        if self._cancel.is_set():
            raise _CancelledError

    # ------------------------------------------------------------ run

    def run(self) -> RipResult:
        started = self._clock()
        spans = track_spans(self.toc, self.settings)
        runs = _runs(spans)
        total = sum(
            d1 - d0 for d0, d1, _ in (_drive_range(r, self.profile.read_offset) for r in runs)
        )
        passes = ("test", "copy") if self.settings.test_and_copy else ("copy",)
        self._emit(RipStarted(tuple(s.number for s in spans), total, passes))
        if self.settings.speed is not None:
            self._set_speed(self.settings.speed, "configured read speed")

        results: dict[str, dict[int, TrackResult]] = {}
        cancelled = False
        try:
            for name in passes:
                results[name] = self._pass(name, runs, total, keep_audio=name == "copy")
        except _CancelledError:
            cancelled = True

        copy = results.get("copy", {})
        test = results.get("test", {})
        tracks = []
        for span in spans:
            r = copy.get(span.number)
            if r is None:
                continue
            t = test.get(span.number)
            if t is not None:
                r = _with_test_crc(r, t.crc32)
            tracks.append(r)
        result = RipResult(
            tuple(tracks),
            self.settings.mode,
            self.settings.test_and_copy,
            cancelled,
            self._clock() - started,
            self.reader.reads,
        )
        self._emit(RipFinished(result))
        return result

    def _pass(
        self, name: str, runs: list[list[_Span]], total: int, *, keep_audio: bool
    ) -> dict[int, TrackResult]:
        self._emit(PassStarted(name))
        done = 0
        pass_start = self._clock()
        finished: dict[int, TrackResult] = {}
        offset = self.profile.read_offset
        for run in runs:
            self._known.clear()
            d0, d1, skip = _drive_range(run, offset)
            states = [_TrackState(s) for s in run]
            idx, pos = 0, 0  # current track in the run, samples written into it
            lba = d0
            while lba < d1:
                self._checkpoint()
                n = min(self.reader.batch, d1 - lba)
                data, statuses = self._block(lba, n)
                self._emit(BlockDone(name, lba, tuple(statuses)))

                for i, status in enumerate(statuses):
                    if status is SectorStatus.SUSPICIOUS:
                        self._note_suspicious(run, states, lba + i, name)
                    elif status is SectorStatus.RECOVERED:
                        st = self._state_for(run, states, lba + i)
                        if st is not None:
                            st.recovered += 1

                # Offset-correct and split into tracks.
                pcm = b"".join(data)
                filled = [s is SectorStatus.FILLED for s in statuses]
                cut = skip * BYTES_PER_SAMPLE if lba == d0 else 0
                pcm = pcm[cut:]
                sector_filled = np.repeat(np.array(filled, dtype=np.bool_), SAMPLES_PER_SECTOR)
                sector_filled = sector_filled[cut // BYTES_PER_SAMPLE :]
                p = 0
                while p < len(pcm) and idx < len(states):
                    st = states[idx]
                    if st.started_at is None:
                        st.started_at = self._clock()
                    take = min(st.span.samples - pos, (len(pcm) - p) // BYTES_PER_SAMPLE)
                    part = pcm[p : p + take * BYTES_PER_SAMPLE]
                    st.checksum.update(part)
                    st.filled_samples += int(
                        sector_filled[p // BYTES_PER_SAMPLE : p // BYTES_PER_SAMPLE + take].sum()
                    )
                    if keep_audio:
                        st.chunks.append(part)
                    p += take * BYTES_PER_SAMPLE
                    pos += take
                    if pos == st.span.samples:
                        finished[st.span.number] = self._finish(name, st, keep_audio)
                        idx, pos = idx + 1, 0

                lba += n
                done += n
                elapsed = max(self._clock() - pass_start, 1e-9)
                self._emit(Progress(name, done, total, lba, done / elapsed / 75.0))
        return finished

    def _finish(self, pass_name: str, st: _TrackState, keep_audio: bool) -> TrackResult:
        elapsed = self._clock() - (st.started_at or self._clock())
        sectors = st.span.end - st.span.start
        result = TrackResult(
            number=st.span.number,
            start=st.span.start,
            sectors=sectors,
            crc32=st.checksum.crc32,
            peak=st.checksum.peak,
            suspicious=tuple(st.suspicious),
            recovered_sectors=st.recovered,
            filled_samples=st.filled_samples,
            speed_x=sectors / elapsed / 75.0 if elapsed > 0 else 0.0,
        )
        if keep_audio and self._on_track is not None:
            self._on_track(st.span.number, b"".join(st.chunks))
        st.chunks.clear()
        self._emit(TrackDone(pass_name, result))
        return result

    def _state_for(
        self, run: list[_Span], states: list[_TrackState], lba: int
    ) -> _TrackState | None:
        """Track holding the first true sample of drive sector ``lba``."""
        sample = lba * SAMPLES_PER_SECTOR - self.profile.read_offset
        sample = min(
            max(sample, run[0].start * SAMPLES_PER_SECTOR), run[-1].end * SAMPLES_PER_SECTOR - 1
        )
        for st in states:
            if st.span.start * SAMPLES_PER_SECTOR <= sample < st.span.end * SAMPLES_PER_SECTOR:
                return st
        return None

    def _note_suspicious(
        self, run: list[_Span], states: list[_TrackState], lba: int, pass_name: str
    ) -> None:
        st = self._state_for(run, states, lba)
        if st is None:
            return
        sample = max(
            lba * SAMPLES_PER_SECTOR - self.profile.read_offset, st.span.start * SAMPLES_PER_SECTOR
        )
        frames = (sample - st.span.start * SAMPLES_PER_SECTOR) // SAMPLES_PER_SECTOR
        stamp = frames_to_timestamp(frames)
        st.suspicious.append(stamp)
        self._emit(SuspiciousPosition(st.span.number, lba, stamp))

    def read_range(self, lba: int, count: int) -> tuple[bytes, list[SectorStatus]]:
        """Raw drive sectors [lba, lba+count) in the configured mode, without offset
        correction or track splitting (offset detection, feature tests)."""
        data: list[bytes] = []
        statuses: list[SectorStatus] = []
        self._known.clear()
        pos = lba
        while pos < lba + count:
            self._checkpoint()
            n = min(self.reader.batch, lba + count - pos)
            d, st = self._block(pos, n)
            data += d
            statuses += st
            pos += n
        return b"".join(data), statuses

    # ------------------------------------------------------------ blocks

    def _block(self, lba: int, n: int) -> tuple[list[bytes], list[SectorStatus]]:
        if self.settings.mode is Mode.BURST:
            return self._burst(lba, n)
        return self._secure(lba, n)

    def _remember(self, lba: int, data: bytes) -> None:
        self._known[lba] = data
        if len(self._known) > _KNOWN_WINDOW * 2:
            cutoff = lba - _KNOWN_WINDOW
            for k in [k for k in self._known if k < cutoff]:
                del self._known[k]

    def _flagged(self, b: Block) -> BoolArray:
        if self.profile.use_c2 and b.c2 is not None:
            return b.failed | b.c2
        return b.failed

    def _burst(self, lba: int, n: int) -> tuple[list[bytes], list[SectorStatus]]:
        b = self.reader.read(lba, n, self._known.get)
        bad = self._flagged(b)
        data, statuses = [], []
        for i in range(n):
            sector = b.sector(i)
            if b.filled[i]:
                statuses.append(SectorStatus.FILLED)
            elif bad[i]:
                statuses.append(SectorStatus.SUSPICIOUS)
            else:
                statuses.append(SectorStatus.OK)
                self._remember(lba + i, sector)
            data.append(sector)
        return data, statuses

    def _secure(self, lba: int, n: int) -> tuple[list[bytes], list[SectorStatus]]:
        r1 = self.reader.read(lba, n, self._known.get)
        bad = self._flagged(r1).copy()
        if not self.profile.fast_path:
            self.reader.defeat_cache(lba, n)
            r2 = self.reader.read(lba, n, self._known.get)
            a = np.frombuffer(r1.audio, dtype="<u4").reshape(n, SAMPLES_PER_SECTOR)
            b = np.frombuffer(r2.audio, dtype="<u4").reshape(n, SAMPLES_PER_SECTOR)
            bad |= self._flagged(r2) | (a != b).any(axis=1)

        data: list[bytes] = []
        statuses: list[SectorStatus] = []
        for i in range(n):
            if r1.filled[i]:
                data.append(_SILENT_SECTOR)
                statuses.append(SectorStatus.FILLED)
            elif bad[i]:
                data.append(_SILENT_SECTOR)
                statuses.append(SectorStatus.SUSPICIOUS)  # placeholder until recovered
            else:
                sector = r1.sector(i)
                data.append(sector)
                statuses.append(SectorStatus.OK)
                self._remember(lba + i, sector)

        i = 0
        while i < n:
            if not (bad[i] and not r1.filled[i]):
                i += 1
                continue
            j = i
            while j < n and bad[j] and not r1.filled[j]:
                j += 1
            for k, (sector, status) in enumerate(self._recover(lba + i, j - i)):
                data[i + k] = sector
                statuses[i + k] = status
            i = j
        return data, statuses

    def _recover(self, lba: int, count: int) -> list[tuple[bytes, SectorStatus]]:
        """Re-read [lba, lba+count) in rounds until each sector has a majority."""
        s = self.settings
        rounds = s.quality.value
        resolved: dict[int, bytes] = {}
        totals: list[Counter[bytes]] = [Counter() for _ in range(count)]
        flagged: list[Counter[bytes]] = [Counter() for _ in range(count)]
        slowed = False
        try:
            for rnd in range(1, rounds + 1):
                pending = [i for i in range(count) if i not in resolved]
                if not pending:
                    break
                self._emit(Retrying(lba, count, rnd, rounds))
                if rnd == 2 and s.recovery_speed is not None and not slowed:
                    slowed = self._set_speed(s.recovery_speed, f"read errors at LBA {lba}")
                votes: list[Counter[bytes]] = [Counter() for _ in range(count)]
                for _ in range(s.reads_per_round):
                    self._checkpoint()
                    self.reader.defeat_cache(lba, count)
                    b = self.reader.read(lba, count, self._known.get)
                    bad = self._flagged(b)
                    for i in pending:
                        if b.failed[i]:
                            continue
                        version = b.sector(i)
                        if bad[i]:
                            flagged[i][version] += 1
                        else:
                            votes[i][version] += 1
                            totals[i][version] += 1
                    if all(
                        votes[i] and votes[i].most_common(1)[0][1] >= s.agree_threshold
                        for i in pending
                    ):
                        break
                for i in pending:
                    if votes[i]:
                        version, hits = votes[i].most_common(1)[0]
                        if hits >= s.agree_threshold:
                            resolved[i] = version
                            self._remember(lba + i, version)
        finally:
            if slowed:
                self._set_speed(s.speed, "error region finished")

        out: list[tuple[bytes, SectorStatus]] = []
        for i in range(count):
            if i in resolved:
                out.append((resolved[i], SectorStatus.RECOVERED))
            elif totals[i]:
                out.append((totals[i].most_common(1)[0][0], SectorStatus.SUSPICIOUS))
            elif flagged[i]:
                out.append((flagged[i].most_common(1)[0][0], SectorStatus.SUSPICIOUS))
            else:
                out.append((_SILENT_SECTOR, SectorStatus.SUSPICIOUS))
        return out

    def _set_speed(self, speed: float | None, reason: str) -> bool:
        if not self.source.set_speed(speed):
            return False
        self._speed = speed
        self._emit(SpeedChanged(speed, reason))
        return True


def _with_test_crc(r: TrackResult, test_crc: int) -> TrackResult:
    return replace(r, test_crc32=test_crc)


def detect_htoa(
    source: AudioSource, profile: DriveProfile, toc: Toc | None = None
) -> HtoaInfo | None:
    """Read the track 1 pre-gap once. None when track 1 starts at LBA 0 (no HTOA)."""
    toc = toc or source.read_toc()
    first = toc.tracks[0]
    if not (first.is_audio and first.number == 1 and first.start > 0):
        return None
    reader = Reader(source, toc, profile)
    level = 0
    lba = 0
    while lba < first.start:
        n = min(reader.batch, first.start - lba)
        level = max(level, peak(reader.read(lba, n).audio))
        lba += n
    return HtoaInfo(first.start, level)
