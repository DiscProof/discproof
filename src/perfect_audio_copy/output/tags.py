"""Vorbis comments and the embedded cover, following MusicBrainz Picard's mapping
(so Plex and Picard read them the same way).

Note Picard's naming: MUSICBRAINZ_TRACKID is the *recording* MBID; the track's own
MBID is MUSICBRAINZ_RELEASETRACKID.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.flac import FLAC, Picture

from perfect_audio_copy import APP_NAME
from perfect_audio_copy.metadata.coverart import Cover
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta

Tags = dict[str, list[str]]


def ar_comment(confidence: int | None, in_database: bool | None) -> str:
    if confidence:
        return f"Ripped with {APP_NAME}, AccurateRip confidence {confidence}"
    if in_database is False:
        return f"Ripped with {APP_NAME}, not in AccurateRip database"
    if in_database:
        return f"Ripped with {APP_NAME}, not verified by AccurateRip"
    return f"Ripped with {APP_NAME}"


def build_tags(
    release: ReleaseMeta,
    track: TrackMeta,
    *,
    disc_id: str | None = None,
    ar_confidence: int | None = None,
    ar_in_database: bool | None = None,
) -> Tags:
    tags: Tags = {}

    def put(key: str, *values: str | int | None) -> None:
        vals = [str(v) for v in values if v not in (None, "")]
        if vals:
            tags[key] = vals

    put("TITLE", track.title or ("Hidden Track" if track.number == 0 else None))
    put("ARTIST", track.artist or release.artist)
    put("ALBUMARTIST", release.artist)
    put("ALBUM", release.title)
    put("TRACKNUMBER", track.number)
    put("TRACKTOTAL", len([t for t in release.tracks if t.number > 0]))
    put("DISCNUMBER", release.disc_number)
    put("DISCTOTAL", release.disc_total)
    put("DATE", release.date)
    put("ORIGINALDATE", release.original_date)
    put("GENRE", release.genre)
    put("LABEL", release.label)
    put("CATALOGNUMBER", release.catalog_number)
    put("BARCODE", release.barcode)
    put("ISRC", track.isrcs[0] if track.isrcs else None)
    put("MEDIA", release.media_format or "CD")
    if release.source is Source.MUSICBRAINZ:
        put("MUSICBRAINZ_ALBUMID", release.release_id)
        put("MUSICBRAINZ_RELEASEGROUPID", release.release_group_id)
        put("MUSICBRAINZ_ALBUMARTISTID", *release.artist_ids)
        put("MUSICBRAINZ_ARTISTID", *(track.artist_ids or release.artist_ids))
        put("MUSICBRAINZ_TRACKID", track.recording_id)
        put("MUSICBRAINZ_RELEASETRACKID", track.track_id)
    elif release.source is Source.DISCOGS:
        put("DISCOGS_RELEASE_ID", release.release_id)
    put("MUSICBRAINZ_DISCID", disc_id)
    put("COMMENT", ar_comment(ar_confidence, ar_in_database))
    return tags


def front_picture(cover: Cover) -> Picture:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = "image/jpeg"
    pic.desc = ""
    pic.width = cover.embed_width
    pic.height = cover.embed_height
    pic.depth = 24
    pic.data = cover.embed_jpeg
    return pic


def write_tags(path: Path, tags: Tags, cover: Cover | None = None) -> None:
    """Replace all Vorbis comments and pictures of a FLAC file."""
    f: Any = FLAC(str(path))  # mutagen is only partly typed
    if f.tags is None:
        f.add_tags()
    vc: Any = f.tags
    vc.clear()
    for key, values in tags.items():
        vc[key] = values
    f.clear_pictures()
    if cover is not None:
        f.add_picture(front_picture(cover))
    f.save()
