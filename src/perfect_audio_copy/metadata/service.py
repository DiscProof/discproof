"""Metadata lookup across sources, and cover art.

Order: MusicBrainz by disc ID -> MusicBrainz by TOC -> Discogs (by the disc's MCN
barcode, else CD-Text artist/album) -> CD-Text. A failing source is recorded in
``errors`` and the next one is tried; the user can always edit everything.
Cover art: Cover Art Archive (release, then release group) -> Discogs images.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from perfect_audio_copy.drive.cdtext import CdText
from perfect_audio_copy.drive.device import DiscInfo
from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.metadata.coverart import Cover, CoverArtArchive, CoverError, prepare_cover
from perfect_audio_copy.metadata.discid import mb_disc_id
from perfect_audio_copy.metadata.discogs import Discogs
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta
from perfect_audio_copy.metadata.musicbrainz import MusicBrainz, preselect
from perfect_audio_copy.metadata.web import ResponseCache, Web, WebError


@dataclass(slots=True)
class LookupResult:
    disc_id: str
    releases: list[ReleaseMeta]
    #: Release to preselect in the chooser; None = the user must choose (or edit).
    preselected: int | None
    errors: list[str] = field(default_factory=list)

    @property
    def unambiguous(self) -> bool:
        """Safe to rip without asking (auto-rip)."""
        return self.preselected is not None and self.releases[self.preselected].source in (
            Source.MUSICBRAINZ,
            Source.DISCOGS,
        )


def release_from_cdtext(cdtext: CdText | None, toc: Toc) -> ReleaseMeta | None:
    if cdtext is None or cdtext.is_empty:
        return None
    album = cdtext.album
    tracks = []
    for t in toc.audio_tracks:
        entry = cdtext.track(t.number)
        tracks.append(
            TrackMeta(
                number=t.number,
                title=entry.title or "",
                artist=entry.performer or album.performer or "",
                isrcs=(entry.code,) if entry.code else (),
            )
        )
    return ReleaseMeta(
        source=Source.CDTEXT,
        title=album.title or "",
        artist=album.performer or "",
        tracks=tracks,
        genre=cdtext.genre or None,
        barcode=album.code or None,
    )


def blank_release(toc: Toc) -> ReleaseMeta:
    """Placeholder the user fills in by hand."""
    tracks = [TrackMeta(t.number, f"Track {t.number:02d}", "") for t in toc.audio_tracks]
    return ReleaseMeta(Source.MANUAL, "", "", tracks)


class MetadataService:
    def __init__(
        self,
        web: Web | None = None,
        *,
        discogs_token: str | None = None,
        use_discogs: bool = True,
    ) -> None:
        self.web = web or Web(cache=ResponseCache())
        self.musicbrainz = MusicBrainz(self.web)
        self.caa = CoverArtArchive(self.web)
        self.discogs = Discogs(self.web, discogs_token) if use_discogs else None

    def lookup(self, disc: DiscInfo) -> LookupResult:
        toc = disc.toc
        result = LookupResult(mb_disc_id(toc), [], None)
        try:
            result.releases = self.musicbrainz.lookup(toc)
        except WebError as exc:
            result.errors.append(f"MusicBrainz: {exc}")

        cdtext = release_from_cdtext(disc.cdtext, toc)
        if not result.releases and self.discogs is not None:
            try:
                found = self.discogs.find(
                    track_count=len(toc.audio_tracks),
                    barcode=disc.mcn,
                    artist=cdtext.artist if cdtext else None,
                    title=cdtext.title if cdtext else None,
                )
            except WebError as exc:
                result.errors.append(f"Discogs: {exc}")
                found = None
            if found:
                result.releases.append(found)
        if cdtext is not None:
            result.releases.append(cdtext)
        if not result.releases:
            result.releases.append(blank_release(toc))

        _apply_disc_isrcs(result.releases, disc)
        online = [r for r in result.releases if r.source in (Source.MUSICBRAINZ, Source.DISCOGS)]
        pick = preselect(online, disc.mcn) if online else None
        if pick is not None:
            result.preselected = result.releases.index(online[pick])
        elif not online:
            result.preselected = 0  # CD-Text or blank: nothing to choose between
        return result

    def cover(self, release: ReleaseMeta) -> Cover | None:
        """Front cover for the release, prepared for output; None if none found."""
        urls: list[str] = []
        if release.source is Source.MUSICBRAINZ:
            try:
                url = self.caa.front_url(release.release_id, release.release_group_id)
            except WebError:
                url = None
            if url:
                urls.append(url)
            if not urls and self.discogs is not None:
                try:
                    alt = self.discogs.find(
                        track_count=len(release.tracks),
                        barcode=release.barcode,
                        artist=release.artist,
                        title=release.title,
                    )
                except WebError:
                    alt = None
                if alt:
                    urls.extend(alt.image_urls)
        urls.extend(release.image_urls)
        for url in urls:
            try:
                data = self.web.get_bytes(url)
                if data:
                    return prepare_cover(data, url)
            except (WebError, CoverError):
                continue
        return None


def _apply_disc_isrcs(releases: list[ReleaseMeta], disc: DiscInfo) -> None:
    """ISRCs read from the disc itself are authoritative: put them first."""
    for r in releases:
        for t in r.tracks:
            code = disc.isrcs.get(t.number)
            if code:
                t.isrcs = (code, *(i for i in t.isrcs if i != code))
