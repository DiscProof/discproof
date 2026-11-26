"""Low-level optical drive access: SG_IO MMC transport, TOC, READ CD, subchannel.

Core package: must not import Qt.
"""

from discproof.drive.cdtext import CdText, CdTextEntry
from discproof.drive.device import DiscInfo, Drive, SectorRead
from discproof.drive.enumerate import DriveInfo, list_drives
from discproof.drive.sense import (
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
from discproof.drive.toc import Toc, TocError, Track

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
