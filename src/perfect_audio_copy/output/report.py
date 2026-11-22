"""Everything the CUE sheet and the rip log are rendered from."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.engine.events import TrackResult
from perfect_audio_copy.engine.profile import DriveProfile, ExtractionSettings
from perfect_audio_copy.metadata.model import ReleaseMeta
from perfect_audio_copy.verify.accuraterip import TrackVerdict
from perfect_audio_copy.verify.offsets import OffsetMatch


@dataclass(frozen=True, slots=True)
class TrackReport:
    number: int  # 0 = hidden track before track 1
    title: str
    artist: str
    filename: str  # relative to the album folder
    start: int  # LBA of INDEX 01 (0 for the hidden track)
    sectors: int  # length of the file, pre-gap of the next track included
    #: Sectors of this track's INDEX 00 (pre-gap), stored at the end of the previous
    #: file. None: not detected (gap detection is optional).
    pregap: int | None = None
    isrc: str | None = None
    preemphasis: bool = False
    copy_permitted: bool = False
    rip: TrackResult | None = None
    ar: TrackVerdict | None = None
    error: str | None = None  # encoding/writing failure

    @property
    def ok(self) -> bool:
        return self.error is None and self.rip is not None and self.rip.ok


@dataclass(frozen=True, slots=True)
class AlbumReport:
    app_version: str
    created: dt.datetime
    drive: str  # "VENDOR MODEL (firmware X)"
    profile: DriveProfile
    settings: ExtractionSettings
    release: ReleaseMeta
    toc: Toc
    tracks: tuple[TrackReport, ...]  # in disc order; track 0 (HTOA) first if ripped
    mb_disc_id: str
    cddb_id: int
    ar_disc: str | None = None  # dBAR-... file name
    ar_in_database: bool | None = None  # None: not checked
    ar_error: str | None = None
    pressings: tuple[OffsetMatch, ...] = ()
    mcn: str | None = None
    flac_level: int = 8
    replaygain: bool = True
    cancelled: bool = False
    notes: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.cancelled and all(t.ok for t in self.tracks)
