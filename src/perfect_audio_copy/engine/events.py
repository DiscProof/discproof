"""Progress events and results emitted by the extraction engine.

The engine calls ``on_event`` with these from its worker thread; the UI layer turns
them into Qt signals. All are immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from perfect_audio_copy.engine.profile import Mode


class SectorStatus(Enum):
    OK = "ok"  # matched on the first comparison
    RECOVERED = "recovered"  # needed re-reads, then a majority agreed
    SUSPICIOUS = "suspicious"  # no majority / unreadable / burst-mode error
    FILLED = "filled"  # not addressable by the drive (offset); silence inserted


@dataclass(frozen=True, slots=True)
class TrackResult:
    number: int  # 0 = hidden track before track 1 (HTOA)
    start: int  # first LBA (INDEX 01; 0 for HTOA)
    sectors: int
    crc32: int  # "Copy CRC" (or the only CRC in a single pass)
    peak: int  # 0..32768
    test_crc32: int | None = None  # Test & Copy only
    suspicious: tuple[str, ...] = ()  # track-relative mm:ss.ff, one per suspicious sector
    recovered_sectors: int = 0
    filled_samples: int = 0
    speed_x: float = 0.0

    @property
    def peak_percent(self) -> float:
        return self.peak * 100.0 / 32768.0

    @property
    def crc_mismatch(self) -> bool:
        return self.test_crc32 is not None and self.test_crc32 != self.crc32

    @property
    def ok(self) -> bool:
        return not self.suspicious and not self.crc_mismatch


@dataclass(frozen=True, slots=True)
class HtoaInfo:
    """Audio before track 1 INDEX 01 (track 1 pre-gap)."""

    sectors: int
    peak: int

    @property
    def silent(self) -> bool:
        return self.peak == 0


@dataclass(frozen=True, slots=True)
class RipResult:
    tracks: tuple[TrackResult, ...]
    mode: Mode
    test_and_copy: bool
    cancelled: bool
    elapsed_s: float
    reads: int  # READ CD commands issued (excluding cache-defeat reads)

    @property
    def ok(self) -> bool:
        return not self.cancelled and all(t.ok for t in self.tracks)


# ---------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class RipStarted:
    tracks: tuple[int, ...]
    sectors: int  # drive sectors per pass
    passes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PassStarted:
    name: str  # "test" or "copy"


@dataclass(frozen=True, slots=True)
class Progress:
    pass_name: str
    done: int  # drive sectors finished in this pass
    total: int
    lba: int
    speed_x: float


@dataclass(frozen=True, slots=True)
class BlockDone:
    """Final status of drive sectors [lba, lba + len(statuses)) — for the sector map."""

    pass_name: str
    lba: int
    statuses: tuple[SectorStatus, ...]


@dataclass(frozen=True, slots=True)
class Retrying:
    lba: int
    count: int
    round: int  # 1-based
    max_rounds: int


@dataclass(frozen=True, slots=True)
class SpeedChanged:
    speed: float | None  # None = drive maximum
    reason: str


@dataclass(frozen=True, slots=True)
class SuspiciousPosition:
    track: int
    lba: int  # drive LBA of the sector
    timestamp: str  # track-relative mm:ss.ff


@dataclass(frozen=True, slots=True)
class TrackDone:
    pass_name: str
    result: TrackResult


@dataclass(frozen=True, slots=True)
class RipFinished:
    result: RipResult


Event = (
    RipStarted
    | PassStarted
    | Progress
    | BlockDone
    | Retrying
    | SpeedChanged
    | SuspiciousPosition
    | TrackDone
    | RipFinished
)
