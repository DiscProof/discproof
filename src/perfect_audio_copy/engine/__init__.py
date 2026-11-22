"""Extraction engine: burst / secure / test-and-copy modes, offset correction,
drive feature detection, gap detection and the VirtualDrive simulator.

Core package: must not import Qt.
"""

from perfect_audio_copy.engine.events import (
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
from perfect_audio_copy.engine.extract import Extractor, detect_htoa
from perfect_audio_copy.engine.profile import (
    CacheDefeat,
    DriveProfile,
    ExtractionSettings,
    Mode,
    Quality,
)
from perfect_audio_copy.engine.source import AudioSource
from perfect_audio_copy.engine.virtual import C2Mode, VirtualDrive, load_wav, make_toc

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
