"""Low-level optical drive access: SG_IO MMC transport, TOC, READ CD, subchannel.

Core package: must not import Qt.
"""

from perfect_audio_copy.drive.cdtext import CdText, CdTextEntry
from perfect_audio_copy.drive.device import DiscInfo, Drive, SectorRead
from perfect_audio_copy.drive.enumerate import DriveInfo, list_drives
from perfect_audio_copy.drive.sense import (
    DriveAccessError,
    DriveError,
    HardwareError,
    IllegalRequestError,
    MediumError,
    MediumNotPresentError,
    NotReadyError,
    ScsiError,
    TransportError,
    UnitAttentionError,
)
from perfect_audio_copy.drive.toc import Toc, TocError, Track

__all__ = [
    "CdText",
    "CdTextEntry",
    "DiscInfo",
    "Drive",
    "DriveAccessError",
    "DriveError",
    "DriveInfo",
    "HardwareError",
    "IllegalRequestError",
    "MediumError",
    "MediumNotPresentError",
    "NotReadyError",
    "ScsiError",
    "SectorRead",
    "Toc",
    "TocError",
    "Track",
    "TransportError",
    "UnitAttentionError",
    "list_drives",
]
