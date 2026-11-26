"""Extraction engine: burst / secure / test-and-copy modes, offset correction,
drive feature detection, gap detection and the VirtualDrive simulator.

Core package: must not import Qt.
"""

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
from discproof.engine.extract import Extractor, detect_htoa
from discproof.engine.profile import (
    CacheDefeat,
    DriveProfile,
    ExtractionSettings,
    Mode,
    Quality,
)
from discproof.engine.source import AudioSource
from discproof.engine.virtual import C2Mode, VirtualDrive, load_wav, make_toc

__all__ = [
    "AudioSource",
    "BlockDone",
    "C2Mode",
    "CacheDefeat",
    "DriveProfile",
    "Event",
    "ExtractionSettings",
    "Extractor",
    "HtoaInfo",
    "Mode",
    "PassStarted",
    "Progress",
    "Quality",
    "Retrying",
    "RipFinished",
    "RipResult",
    "RipStarted",
    "SectorStatus",
    "SpeedChanged",
    "SuspiciousPosition",
    "TrackDone",
    "TrackResult",
    "VirtualDrive",
    "detect_htoa",
    "load_wav",
    "make_toc",
]
