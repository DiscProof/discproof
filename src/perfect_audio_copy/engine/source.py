"""The interface the engine reads from: a real :class:`~perfect_audio_copy.drive.Drive`
or a :class:`~perfect_audio_copy.engine.virtual.VirtualDrive`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from perfect_audio_copy.drive.device import SectorRead
from perfect_audio_copy.drive.toc import Toc


class AudioSource(Protocol):
    def read_toc(self) -> Toc: ...

    def read_cd(self, lba: int, count: int, *, c2: bool = False, subq: bool = False) -> SectorRead:
        """Read ``count`` CD-DA sectors. Raises MediumError on unreadable sectors and
        IllegalRequestError for LBAs the drive will not address."""
        ...

    def flush_cache(self, lba: int) -> bool:
        """READ(12) FUA for ``lba``. False if the drive rejects it."""
        ...

    def set_speed(self, multiplier: float | None) -> bool: ...


if TYPE_CHECKING:
    from perfect_audio_copy.drive.device import Drive

    def _drive_is_an_audio_source(d: Drive) -> AudioSource:
        return d  # mypy proves the real drive satisfies the protocol
