"""Per-drive behaviour (DriveProfile) and per-rip choices (ExtractionSettings).

Every hardware behaviour that differs between drives is a profile field with a
conservative default. The Phase 3 feature-detection wizard fills these in and they
are persisted per drive (vendor + model + firmware).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CacheDefeat(Enum):
    """How to make the drive read the disc again instead of answering from its cache."""

    #: Read one sector far away so the drive refills its cache elsewhere. Works on
    #: every drive seen so far (including ones that ignore FUA, e.g. HL-DT-ST GP75N).
    SEEK_AWAY = "seek-away"
    #: READ(12) with Force Unit Access. Cheaper, but many drives accept it and ignore it.
    FUA = "fua"


class Mode(Enum):
    BURST = "burst"
    SECURE = "secure"


class Quality(Enum):
    """Error-recovery quality: the maximum number of 16-read rounds per bad sector."""

    LOW = 1
    MEDIUM = 3
    HIGH = 5


@dataclass(frozen=True, slots=True)
class DriveProfile:
    #: Read offset correction in samples, AccurateRip convention: a drive with +X
    #: returns data X samples too early, so we read X samples later.
    read_offset: int = 0
    #: Whether the drive returns data for LBAs before 0 / at or after the lead-out.
    overread_leadin: bool = False
    overread_leadout: bool = False
    #: Repeated reads from different start points land on the same samples.
    accurate_stream: bool = True
    #: Whether the drive answers repeated audio reads from its cache.
    caches_audio: bool = True
    cache_defeat: CacheDefeat = CacheDefeat.SEEK_AWAY
    #: Sectors the cache can hold (including read-ahead). Seek-away reads go at least
    #: this far from the region being read.
    cache_sectors: int = 2000
    #: The drive returns C2 error pointers at all / they have been shown to be reliable.
    c2_supported: bool = False
    c2_trusted: bool = False
    #: Sectors per READ CD command.
    batch_sectors: int = 26
    #: For drives without accurate stream: extra sectors read on each side of a
    #: region so reads can be aligned against already-known audio.
    sync_sectors: int = 2
    #: Largest start misalignment (in samples) to search for when aligning.
    max_jitter: int = 588

    @property
    def use_c2(self) -> bool:
        return self.c2_supported and self.c2_trusted

    @property
    def fast_path(self) -> bool:
        """EAC's fastest secure mode: read once, re-read only what C2 flags."""
        return self.accurate_stream and not self.caches_audio and self.use_c2


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    mode: Mode = Mode.SECURE
    #: A full test pass before the copy pass; per-track CRCs of both are compared.
    test_and_copy: bool = False
    quality: Quality = Quality.HIGH
    reads_per_round: int = 16
    #: Reads in one round that must agree for a sector to be accepted.
    agree_threshold: int = 8
    #: Read speed multiplier; None = drive maximum.
    speed: float | None = None
    #: Speed used while recovering a region that failed its first recovery round.
    #: None = don't change speed.
    recovery_speed: float | None = 4.0
    #: Rip the hidden track before track 1 (track 1 INDEX 01 > 0) as track 0.
    rip_htoa: bool = False
    #: Tracks to rip; None = every audio track.
    tracks: tuple[int, ...] | None = None
