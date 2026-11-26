"""Drive feature detection: caching, accurate stream, C2 pointers, overread.

Results are measurements on the inserted disc; :meth:`FeatureReport.apply` turns
them into a :class:`~discproof.engine.profile.DriveProfile`. Anything that
cannot be established is reported as such and the conservative profile value kept.

Caching is detected by timing: a re-read that completes faster than the drive could
possibly read the disc (half the media time at its maximum speed) came from the
cache. The same test, after a FUA flush or a far-away read, shows whether that
method empties the cache.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np
import numpy.typing as npt

from discproof.drive.constants import C2_SIZE, SAMPLES_PER_SECTOR, SECTOR_SIZE
from discproof.drive.sense import DriveError, IllegalRequestError
from discproof.drive.toc import Toc
from discproof.engine.profile import CacheDefeat, DriveProfile
from discproof.engine.reader import seek_away_lba
from discproof.engine.source import AudioSource

U32Array = npt.NDArray[np.uint32]

PROBE_SECTORS = 26
#: Re-read size for the cache test: one engine batch. Some caches hold little more
#: than the last command (the GP75N keeps ~26 sectors), so bigger re-reads miss.
TIMING_SECTORS = PROBE_SECTORS
READAHEAD_STEPS = (26, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
STREAM_SECTORS = 40
STREAM_STARTS = (7, 13)
C2_READS = 5


@dataclass(frozen=True, slots=True)
class CacheResult:
    caches_audio: bool
    consistent: bool  # all test positions agreed
    cache_sectors: int | None = None  # estimated cache window (read-ahead), if caching
    fua_works: bool | None = None  # None: not tested (no cache) / rejected -> False
    seek_away_works: bool | None = None


@dataclass(frozen=True, slots=True)
class C2Result:
    supported: bool
    errors_seen: int = 0  # sector reads that differed from the clean reference
    missed: int = 0  # ... of which C2 did not flag
    false_flags: int = 0  # flagged sector reads whose data was fine
    sectors_tested: int = 0

    @property
    def tested(self) -> bool:
        """Errors occurred, so C2 could be judged. A clean disc proves nothing."""
        return self.errors_seen > 0

    @property
    def trusted(self) -> bool:
        return self.supported and self.tested and self.missed == 0


@dataclass(frozen=True, slots=True)
class FeatureReport:
    cache: CacheResult
    accurate_stream: bool
    accurate_stream_consistent: bool
    c2: C2Result
    overread_leadin: bool
    overread_leadout: bool
    notes: tuple[str, ...] = field(default=())

    def apply(self, profile: DriveProfile) -> DriveProfile:
        cache = self.cache
        defeat = CacheDefeat.FUA if cache.fua_works else CacheDefeat.SEEK_AWAY
        return replace(
            profile,
            caches_audio=cache.caches_audio,
            cache_defeat=defeat,
            cache_sectors=cache.cache_sectors or profile.cache_sectors,
            accurate_stream=self.accurate_stream,
            c2_supported=self.c2.supported,
            c2_trusted=self.c2.trusted,
            overread_leadin=self.overread_leadin,
            overread_leadout=self.overread_leadout,
        )


class FeatureDetector:
    def __init__(
        self,
        source: AudioSource,
        toc: Toc,
        *,
        max_speed_x: float = 48.0,
        clock: Callable[[], float] = time.monotonic,
        on_step: Callable[[str], None] | None = None,
    ) -> None:
        self.source = source
        self.toc = toc
        self.max_speed_x = max_speed_x
        self.clock = clock
        self._step = on_step or (lambda _s: None)
        audio = toc.audio_tracks
        if not audio:
            raise ValueError("disc has no audio tracks")
        self.lo = audio[0].start
        self.hi = toc.audio_leadout
        self.notes: list[str] = []
        self._cache = CacheResult(caches_audio=True, consistent=False)

    # ------------------------------------------------------------ helpers

    def positions(self) -> list[int]:
        span = self.hi - self.lo
        need = STREAM_SECTORS + max(STREAM_STARTS) + max(READAHEAD_STEPS[:4]) + 2 * PROBE_SECTORS
        out = []
        for frac in (0.25, 0.5, 0.75):
            p = self.lo + int(span * frac)
            p = min(max(p, self.lo + max(STREAM_STARTS)), self.hi - need)
            out.append(p)
        return sorted(set(out))

    def _read(self, lba: int, count: int, *, c2: bool = False) -> tuple[bytes, list[bytes]] | None:
        """Audio, plus each sector's C2 pointer bytes (one bit per audio byte) if asked."""
        audio = bytearray()
        pointers: list[bytes] = []
        pos = lba
        try:
            while pos < lba + count:
                n = min(PROBE_SECTORS, lba + count - pos)
                r = self.source.read_cd(pos, n, c2=c2)
                audio += r.audio
                if c2:
                    for i in range(n):
                        pointers.append(r.c2_for(i) or bytes(C2_SIZE))
                pos += n
        except DriveError:
            return None
        return bytes(audio), pointers

    def _timed(self, lba: int, count: int) -> tuple[float | None, bytes | None]:
        t0 = self.clock()
        got = self._read(lba, count)
        if got is None:
            return None, None
        return self.clock() - t0, got[0]

    def _from_cache(self, elapsed: float | None, count: int, cold: float | None = None) -> bool:
        """Faster than the disc can deliver at full speed, or a tenth of a cold read."""
        if elapsed is None:
            return False
        media = count / (75.0 * self.max_speed_x)
        if elapsed < 0.5 * media:
            return True
        return cold is not None and elapsed < min(media, 0.1 * cold)

    def _cold(self) -> None:
        """Move the drive's cache to the end of the disc (beyond every test region)."""
        self._read(self.hi - 1, 1)

    def _defeat(self, lba: int, count: int) -> None:
        c = self._cache
        if not c.caches_audio:
            return
        if c.fua_works and self.source.flush_cache(lba):
            return
        window = (c.cache_sectors or 4096) + PROBE_SECTORS
        self._read(seek_away_lba(lba, count, window, self.lo, self.hi), 1)

    # ------------------------------------------------------------ tests

    def detect_cache(self) -> CacheResult:
        self._step("Testing whether the drive caches audio")
        hits = []
        for p in self.positions():
            self._cold()
            t_cold, cold = self._timed(p, TIMING_SECTORS)
            t_warm, warm = self._timed(p, TIMING_SECTORS)
            hit = self._from_cache(t_warm, TIMING_SECTORS, t_cold)
            if hit and cold is not None and warm is not None and cold != warm:
                hit = False  # different data cannot have come from a cache
            hits.append(hit)
        if all(hits) or not any(hits):
            caches, consistent = hits[0], True
        else:
            caches, consistent = True, False
            self.notes.append("Cache test inconsistent; assuming the drive caches audio.")
        if not caches:
            self._cache = CacheResult(False, consistent)
            return self._cache

        p = self.positions()[0]
        self._step("Estimating the cache size")
        extent = PROBE_SECTORS
        for k in READAHEAD_STEPS:
            if p + k + PROBE_SECTORS > self.hi:
                break
            self._cold()
            self._read(p, PROBE_SECTORS)
            if not self._from_cache(self._timed(p + k, PROBE_SECTORS)[0], PROBE_SECTORS):
                break
            extent = k + PROBE_SECTORS
        cache_sectors = 1 << max(8, (2 * extent - 1).bit_length())  # x2 margin, >= 256

        self._step("Testing cache flush (FUA)")
        self._cold()
        self._read(p, PROBE_SECTORS)
        fua_works = self.source.flush_cache(p) and not self._from_cache(
            self._timed(p, PROBE_SECTORS)[0], PROBE_SECTORS
        )

        self._step("Testing cache defeat by reading elsewhere")
        self._cold()
        self._read(p, PROBE_SECTORS)
        window = cache_sectors + PROBE_SECTORS
        self._read(seek_away_lba(p, PROBE_SECTORS, window, self.lo, self.hi), 1)
        seek_ok = not self._from_cache(self._timed(p, PROBE_SECTORS)[0], PROBE_SECTORS)
        if not (fua_works or seek_ok):
            self.notes.append("Could not empty the drive cache: secure mode may be unreliable.")
        self._cache = CacheResult(True, consistent, cache_sectors, fua_works, seek_ok)
        return self._cache

    def detect_accurate_stream(self, max_shift: int = 2 * SAMPLES_PER_SECTOR) -> tuple[bool, bool]:
        """Read the same audio from different start sectors and see whether it lines
        up. Returns (accurate, consistent)."""
        self._step("Testing accurate stream")
        verdicts: list[bool] = []
        for p in self.positions():
            ref = self._read(p, STREAM_SECTORS)
            if ref is None:
                continue
            for s in STREAM_STARTS:
                self._defeat(p - s, STREAM_SECTORS)
                other = self._read(p - s, STREAM_SECTORS)
                if other is None:
                    continue
                n = (STREAM_SECTORS - s) * SECTOR_SIZE
                a = ref[0][:n]
                b = other[0][s * SECTOR_SIZE : s * SECTOR_SIZE + n]
                if a == b:
                    verdicts.append(True)
                    continue
                shift = _relative_shift(
                    np.frombuffer(ref[0], "<u4"),
                    np.frombuffer(other[0], "<u4"),
                    s * SAMPLES_PER_SECTOR,
                    max_shift,
                )
                if shift not in (None, 0):
                    verdicts.append(False)
                # None/0: read errors rather than misalignment; inconclusive.
        if not verdicts:
            self.notes.append("Accurate stream could not be tested; assuming it is absent.")
            return False, False
        accurate = all(verdicts)
        consistent = all(verdicts) or not any(verdicts)
        if not consistent:
            self.notes.append(
                "Reads were only sometimes misaligned: treating as no accurate stream."
            )
        return accurate, consistent

    def detect_c2(self, lbas: list[int] | None = None, reads: int = C2_READS) -> C2Result:
        """C2 support, and — if read errors occur in the tested regions — whether the
        flags match the sectors that actually came back wrong."""
        self._step("Testing C2 error pointers")
        probe = self.positions()[0]
        try:
            self.source.read_cd(probe, 1, c2=True)
        except IllegalRequestError:
            return C2Result(False)
        except DriveError:
            pass
        starts = lbas if lbas is not None else self.positions()
        errors = missed = false_flags = sectors = 0
        for start in starts:
            n = min(PROBE_SECTORS, self.hi - start)
            versions: list[list[bytes]] = [[] for _ in range(n)]
            pointers: list[list[bytes]] = [[] for _ in range(n)]
            for _ in range(reads):
                self._defeat(start, n)
                got = self._read(start, n, c2=True)
                if got is None:
                    continue
                audio, fl = got
                for i in range(n):
                    versions[i].append(audio[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE])
                    pointers[i].append(fl[i])
            for i in range(n):
                verdict = _judge_c2(versions[i], pointers[i])
                if verdict is None:
                    continue
                sectors += 1
                errors += verdict[0]
                missed += verdict[1]
                false_flags += verdict[2]
        return C2Result(True, errors, missed, false_flags, sectors)

    def detect_overread(self) -> tuple[bool, bool]:
        self._step("Testing lead-in / lead-out overread")
        lead_in = self._read(-1, 1) is not None
        lead_out = False
        if not self.toc.is_enhanced:
            lead_out = self._read(self.toc.leadout, 1) is not None
        return lead_in, lead_out

    def run(self) -> FeatureReport:
        cache = self.detect_cache()
        accurate, consistent = self.detect_accurate_stream()
        c2 = self.detect_c2()
        if c2.supported and not c2.tested:
            self.notes.append(
                "C2 is supported but the disc read without errors, so its reliability is "
                "unknown; test with a scratched disc before trusting it."
            )
        lead_in, lead_out = self.detect_overread()
        return FeatureReport(cache, accurate, consistent, c2, lead_in, lead_out, tuple(self.notes))


def _judge_c2(versions: list[bytes], pointers: list[bytes]) -> tuple[int, int, int] | None:
    """Score one sector's reads: (wrong reads, wrong reads C2 missed, flagged reads
    that were fine), or None if the sector cannot be judged.

    The reference is the majority among the *unflagged* reads only. Drives conceal
    uncorrectable errors, and the concealment is often repeatable: on a scratch the
    flagged reads can be identical and outnumber the clean ones. A plain majority
    would then take the concealed data as correct and count every clean read as a
    missed error.

    C2 is checked byte by byte: a wrong read is only caught if every byte that
    differs from the reference has its C2 bit set. A flagged read whose errors lie
    partly outside its pointers counts as missed. So does a reference that is itself
    wrong (e.g. the only clean read): correctly flagged reads then differ from it at
    bytes they did not flag, which exposes it.
    """
    flagged = [any(c) for c in pointers]
    clean = [v for v, f in zip(versions, flagged, strict=True) if not f]
    if not clean:
        return None  # never read without a flag: nothing to compare against
    reference, _ = Counter(clean).most_common(1)[0]
    ref = np.frombuffer(reference, dtype=np.uint8)
    errors = missed = false_flags = 0
    for v, c, f in zip(versions, pointers, flagged, strict=True):
        diff = np.frombuffer(v, dtype=np.uint8) != ref
        if not diff.any():
            false_flags += f
            continue
        errors += 1
        bits = np.unpackbits(np.frombuffer(c, dtype=np.uint8)).astype(np.bool_)
        missed += bool((diff & ~bits).any())
    return errors, missed, false_flags


def _relative_shift(ref: U32Array, other: U32Array, base: int, max_shift: int) -> int | None:
    """``other`` started ``base`` samples before ``ref``. Find d with
    other[base + i + d] == ref[i] over the middle of the overlap."""
    win = ref[max_shift : max_shift + SAMPLES_PER_SECTOR]
    if len(win) < SAMPLES_PER_SECTOR or (win == win[0]).all():
        return None
    for d in sorted(range(-max_shift, max_shift + 1), key=abs):
        a = base + max_shift + d
        if a < 0 or a + len(win) > len(other):
            continue
        if np.array_equal(other[a : a + len(win)], win):
            return d
    return None
