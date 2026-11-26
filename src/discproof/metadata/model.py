"""Source-independent album metadata. Mutable: the UI lets the user edit every field."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(Enum):
    MUSICBRAINZ = "musicbrainz"
    DISCOGS = "discogs"
    CDTEXT = "cd-text"
    MANUAL = "manual"


@dataclass(slots=True)
class TrackMeta:
    number: int
    title: str
    artist: str
    artist_ids: tuple[str, ...] = ()
    isrcs: tuple[str, ...] = ()  # the database's; the disc's own ISRC wins at tag time
    recording_id: str | None = None
    track_id: str | None = None
    length_ms: int | None = None


@dataclass(slots=True)
class ReleaseMeta:
    source: Source
    title: str
    artist: str  # album artist credit
    tracks: list[TrackMeta]
    artist_ids: tuple[str, ...] = ()
    release_id: str | None = None  # MusicBrainz release MBID or Discogs release id
    release_group_id: str | None = None
    disc_id: str | None = None  # MusicBrainz disc ID, when the release lists it
    date: str | None = None  # YYYY[-MM[-DD]]
    original_date: str | None = None
    country: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    barcode: str | None = None
    media_format: str | None = None
    disc_number: int = 1
    disc_total: int = 1
    genre: str | None = None
    status: str | None = None
    disambiguation: str | None = None
    #: Matched by disc ID (exact) rather than by a fuzzy TOC/search match.
    exact: bool = False
    #: Cover candidates the source itself provides (Discogs), best first.
    image_urls: tuple[str, ...] = ()
    #: MusicBrainz: whether the Cover Art Archive has a front image (None: unknown).
    cover_front: bool | None = None

    @property
    def year(self) -> str | None:
        return self.date[:4] if self.date else None

    def summary(self) -> str:
        """One line for the release chooser."""
        parts = [
            " ".join(p for p in (self.country, self.date) if p),
            " ".join(p for p in (self.label, self.catalog_number) if p),
            self.barcode or "",
            self.media_format or "",
        ]
        if self.disc_total > 1:
            parts.append(f"disc {self.disc_number}/{self.disc_total}")
        if self.disambiguation:
            parts.append(f"({self.disambiguation})")
        return " · ".join(p for p in parts if p)


def same_barcode(a: str | None, b: str | None) -> bool:
    """UPC-A (12 digits) and EAN-13 (13 digits, leading 0) of the same product match."""
    if not a or not b:
        return False
    da = "".join(c for c in a if c.isdigit()).lstrip("0")
    db = "".join(c for c in b if c.isdigit()).lstrip("0")
    return bool(da) and da == db
