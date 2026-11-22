"""VirtualDrive: a simulated CD drive serving a PCM disc image.

It implements :class:`~perfect_audio_copy.engine.source.AudioSource` and can simulate
the drive behaviours the engine has to cope with:

* a **read offset** (AccurateRip convention: +X returns data X samples too early),
* **lead-in / lead-out overread** support (or LBA-out-of-range errors),
* **jitter**: a fresh read that starts a few samples off on drives without accurate stream,
* a **cache** that answers repeated reads with the same data. It holds one contiguous
  segment (the last disc read plus read-ahead), like the drives seen so far, and
  ``fua_flushes=False`` models drives that accept FUA but ignore it,
* **errors**: random transient errors, weak sectors (wrong with some probability),
  bad sectors (always wrong, differently every time) and unreadable sectors
  (MEDIUM ERROR),
* **C2 pointers** that are absent, accurate, or lie (miss errors / flag good data),
* error rates that drop at lower read speeds.

Error positions (``weak``, ``bad``, ``unreadable``) are *disc* sector indices, i.e.
positions in the true audio, independent of the simulated offset.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
import numpy.typing as npt

from perfect_audio_copy.drive.constants import (
    BYTES_PER_SAMPLE,
    CTRL_DATA,
    SAMPLES_PER_SECTOR,
    SECTOR_SIZE,
    SUBQ_SIZE,
)
from perfect_audio_copy.drive.crc import cd_crc16
from perfect_audio_copy.drive.device import SectorRead
from perfect_audio_copy.drive.msf import int_to_bcd, lba_to_msf
from perfect_audio_copy.drive.sense import IllegalRequestError, MediumError, Sense
from perfect_audio_copy.drive.toc import Toc, Track

_READ_CD = 0xBE
_LBA_OUT_OF_RANGE = Sense(0x5, 0x21, 0x00)
_INVALID_FIELD = Sense(0x5, 0x24, 0x00)
_UNRECOVERED_READ = Sense(0x3, 0x11, 0x00)

U32Array = npt.NDArray[np.uint32]
BoolArray = npt.NDArray[np.bool_]


class C2Mode(Enum):
    NONE = "none"  # drive rejects C2 requests
    ACCURATE = "accurate"
    LYING = "lying"  # misses some errors and flags some good sectors


def make_toc(lengths: list[int], *, first_start: int = 0, data_track: int | None = None) -> Toc:
    """A single-session TOC with consecutive tracks of the given sector lengths."""
    tracks = []
    lba = first_start
    for i, length in enumerate(lengths, start=1):
        tracks.append(Track(i, lba, CTRL_DATA if i == data_track else 0))
        lba += length
    return Toc(tuple(tracks), ((1, lba),))


def load_wav(path: str | Path) -> bytes:
    """PCM bytes of a 16-bit stereo 44.1 kHz WAV file."""
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (2, 2, 44100):
            raise ValueError(f"{path}: not 16-bit stereo 44.1 kHz audio")
        return w.readframes(w.getnframes())


@dataclass(slots=True)
class _Segment:
    """Contiguous data the drive read from the disc (and keeps in its cache)."""

    lba: int  # nominal LBA of the first sector
    samples: U32Array
    c2: BoolArray  # one flag per audio byte

    @property
    def sectors(self) -> int:
        return len(self.samples) // SAMPLES_PER_SECTOR


@dataclass(slots=True)
class VirtualDriveStats:
    commands: int = 0
    disc_reads: int = 0  # reads that went to the disc rather than the cache
    sectors_returned: int = 0
    flushes: int = 0
    speed_changes: int = 0


class VirtualDrive:
    def __init__(
        self,
        audio: bytes,
        toc: Toc,
        *,
        offset: int = 0,
        overread_leadin: bool = False,
        overread_leadout: bool = False,
        overread_limit: int = 100,
        jitter: float = 0.0,
        max_jitter: int = 0,
        cache_sectors: int = 0,
        fua_flushes: bool = True,
        error_rate: float = 0.0,
        weak: dict[int, float] | None = None,
        bad: set[int] | frozenset[int] = frozenset(),
        unreadable: set[int] | frozenset[int] = frozenset(),
        c2: C2Mode = C2Mode.ACCURATE,
        c2_miss_rate: float = 0.0,
        c2_false_rate: float = 0.0,
        max_speed: float = 24.0,
        pregaps: dict[int, int] | None = None,
        seed: int = 0,
    ) -> None:
        if len(audio) % SECTOR_SIZE:
            raise ValueError("disc image must be a whole number of sectors")
        self.toc = toc
        self._disc = np.frombuffer(audio, dtype="<u4")
        self.offset = offset
        self.overread_leadin = overread_leadin
        self.overread_leadout = overread_leadout
        self.overread_limit = overread_limit
        self.jitter = jitter
        self.max_jitter = max_jitter
        self.cache_sectors = cache_sectors
        self.fua_flushes = fua_flushes
        self.error_rate = error_rate
        self.weak = dict(weak or {})
        self.bad = frozenset(bad)
        self.unreadable = frozenset(unreadable)
        self.c2_mode = c2
        self.c2_miss_rate = c2_miss_rate
        self.c2_false_rate = c2_false_rate
        self.max_speed = max_speed
        self.speed: float | None = None
        self.pregaps = dict(pregaps or {})
        self.stats = VirtualDriveStats()
        self._rng = np.random.default_rng(seed)
        self._cache: _Segment | None = None
        #: Start misalignment (samples) of each disc read, most recent last.
        self.jitter_log: list[int] = []

    @classmethod
    def from_wav(cls, path: str | Path, toc: Toc, **kwargs: object) -> VirtualDrive:
        return cls(load_wav(path), toc, **kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------ AudioSource

    def read_toc(self) -> Toc:
        return self.toc

    def flush_cache(self, lba: int) -> bool:
        self.stats.flushes += 1
        if self.fua_flushes:
            self._cache = None
        return True

    def set_speed(self, multiplier: float | None) -> bool:
        self.speed = multiplier
        self.stats.speed_changes += 1
        return True

    def read_cd(self, lba: int, count: int, *, c2: bool = False, subq: bool = False) -> SectorRead:
        self.stats.commands += 1
        if count <= 0:
            raise IllegalRequestError(_READ_CD, _INVALID_FIELD)
        if c2 and self.c2_mode is C2Mode.NONE:
            raise IllegalRequestError(_READ_CD, _INVALID_FIELD)
        lo, hi = self._addressable()
        if lba < lo or lba + count > hi:
            raise IllegalRequestError(_READ_CD, _LBA_OUT_OF_RANGE)

        seg = self._cache
        if seg is None or not (seg.lba <= lba and lba + count <= seg.lba + seg.sectors):
            seg = self._disc_read(lba, count, hi)
        start = (lba - seg.lba) * SAMPLES_PER_SECTOR
        samples = seg.samples[start : start + count * SAMPLES_PER_SECTOR]
        self.stats.sectors_returned += count
        c2_bytes = None
        if c2:
            mask = seg.c2[start * BYTES_PER_SAMPLE : (start + count * SAMPLES_PER_SECTOR) * 4]
            c2_bytes = np.packbits(mask).tobytes()
        qs = tuple(self.q_record(lba + i) for i in range(count)) if subq else None
        return SectorRead(lba, count, samples.astype("<u4").tobytes(), c2_bytes, qs)

    # ------------------------------------------------------------ internals

    def _addressable(self) -> tuple[int, int]:
        leadout = self.toc.leadout
        lo = -self.overread_limit if self.overread_leadin else 0
        hi = leadout + self.overread_limit if self.overread_leadout else leadout
        return lo, hi

    def _error_scale(self) -> float:
        if self.speed is None:
            return 1.0
        return min(1.0, self.speed / self.max_speed)

    def _disc_read(self, lba: int, count: int, hi: int) -> _Segment:
        """Read from the disc: the requested sectors plus read-ahead into the cache."""
        self.stats.disc_reads += 1
        shift = 0
        if self.max_jitter and self._rng.random() < self.jitter:
            shift = int(self._rng.integers(1, self.max_jitter + 1)) * int(self._rng.choice((-1, 1)))
        self.jitter_log.append(shift)
        span = max(count, min(self.cache_sectors, hi - lba))
        first = lba * SAMPLES_PER_SECTOR - self.offset + shift
        n = span * SAMPLES_PER_SECTOR

        # True disc sectors touched by this read; the requested ones must be readable,
        # read-ahead stops before the first unreadable one.
        def disc_sector(sample: int) -> int:
            return sample // SAMPLES_PER_SECTOR

        need_end = first + count * SAMPLES_PER_SECTOR
        for k in range(disc_sector(first), disc_sector(need_end - 1) + 1):
            if k in self.unreadable:
                raise MediumError(_READ_CD, _UNRECOVERED_READ)
        for k in range(disc_sector(need_end), disc_sector(first + n - 1) + 1):
            if k in self.unreadable:
                n = (k * SAMPLES_PER_SECTOR - first) // SAMPLES_PER_SECTOR * SAMPLES_PER_SECTOR
                break

        samples = np.zeros(n, dtype=np.uint32)
        lo_s, hi_s = max(first, 0), min(first + n, len(self._disc))
        if hi_s > lo_s:
            samples[lo_s - first : hi_s - first] = self._disc[lo_s:hi_s]
        c2 = np.zeros(n * BYTES_PER_SAMPLE, dtype=np.bool_)

        scale = self._error_scale()
        for k in range(disc_sector(first), disc_sector(first + n - 1) + 1):
            p = 1.0 if k in self.bad else max(self.error_rate, self.weak.get(k, 0.0)) * scale
            a = max(k * SAMPLES_PER_SECTOR, first) - first
            b = min((k + 1) * SAMPLES_PER_SECTOR, first + n) - first
            corrupted = p > 0 and self._rng.random() < p
            if corrupted:
                self._corrupt(samples, c2, a, b)
            elif self.c2_mode is C2Mode.LYING and self._rng.random() < self.c2_false_rate:
                pos = int(self._rng.integers(a, b)) * BYTES_PER_SAMPLE
                c2[pos : pos + BYTES_PER_SAMPLE] = True
        seg = _Segment(lba, samples, c2)
        self._cache = seg if self.cache_sectors else None
        return seg

    def _corrupt(self, samples: U32Array, c2: BoolArray, a: int, b: int) -> None:
        hits = self._rng.integers(a, b, size=int(self._rng.integers(1, 9)))
        noise = self._rng.integers(1, 2**32, size=len(hits), dtype=np.uint64).astype(np.uint32)
        samples[hits] ^= noise
        flag = not (self.c2_mode is C2Mode.LYING and self._rng.random() < self.c2_miss_rate)
        if flag:
            for h in hits:
                c2[int(h) * BYTES_PER_SAMPLE : (int(h) + 1) * BYTES_PER_SAMPLE] = True

    def q_record(self, lba: int) -> bytes:
        """Formatted Q (ADR 1) for a nominal LBA, honouring ``pregaps`` as INDEX 00."""
        track, index, rel = self._position(lba)
        rm, rest = divmod(abs(rel), 75 * 60)
        rs, rf = divmod(rest, 75)
        am, as_, af = lba_to_msf(max(lba, -150))
        ctrl = track.control if track else 0
        body = bytes(
            [
                (ctrl << 4) | 1,
                0xAA if track is None else int_to_bcd(track.number),
                int_to_bcd(index),
                int_to_bcd(rm),
                int_to_bcd(rs),
                int_to_bcd(rf),
                0,
                int_to_bcd(am),
                int_to_bcd(as_),
                int_to_bcd(af),
            ]
        )
        return (body + cd_crc16(body).to_bytes(2, "big")).ljust(SUBQ_SIZE, b"\x00")

    def _position(self, lba: int) -> tuple[Track | None, int, int]:
        if lba >= self.toc.leadout:
            return None, 1, lba - self.toc.leadout
        tracks = self.toc.tracks
        for i, t in enumerate(tracks):
            nxt = tracks[i + 1] if i + 1 < len(tracks) else None
            if nxt is not None:
                gap_start = nxt.start - self.pregaps.get(nxt.number, 0)
                if gap_start <= lba < nxt.start:
                    return nxt, 0, nxt.start - lba
            if lba < t.start:
                return t, 0, t.start - lba  # track 1 pre-gap
            if nxt is None or lba < nxt.start:
                return t, 1, lba - t.start
        return tracks[-1], 1, lba - tracks[-1].start
