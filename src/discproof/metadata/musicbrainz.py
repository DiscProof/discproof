"""MusicBrainz web service (WS/2, JSON): lookup by disc ID, then by TOC (fuzzy)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from discproof.drive.toc import Toc
from discproof.metadata.discid import mb_disc_id, mb_toc_string
from discproof.metadata.model import ReleaseMeta, Source, TrackMeta, same_barcode
from discproof.metadata.web import Web

API = "https://musicbrainz.org/ws/2"
INC = "artists+recordings+release-groups+labels+isrcs"

#: Medium formats that can hold this disc (a CD-sized audio disc).
CD_FORMATS = {
    "CD",
    "Enhanced CD",
    "Copy Control CD",
    "HDCD",
    "SHM-CD",
    "Blu-spec CD",
    "Blu-spec CD2",
    "HQCD",
    "CD-R",
    "Data CD",
    "DualDisc",
    "Hybrid SACD",
    "Hybrid SACD (CD layer)",
    "8cm CD",
    "Minimax CD",
    "SHM-SACD",
    "UHQCD",
}


def credit(ac: list[dict[str, Any]] | None) -> tuple[str, tuple[str, ...]]:
    """Joined artist-credit name and the artist MBIDs."""
    if not ac:
        return "", ()
    name = "".join(f"{c.get('name', '')}{c.get('joinphrase', '')}" for c in ac)
    ids = tuple(c["artist"]["id"] for c in ac if c.get("artist", {}).get("id"))
    return name, ids


def _pick_medium(
    release: dict[str, Any], disc_id: str | None, track_count: int
) -> tuple[int, dict[str, Any], bool] | None:
    """(position, medium, exact): the medium carrying this disc ID, else the first CD
    medium with the right number of tracks."""
    media = release.get("media") or []
    if disc_id:
        for m in media:
            if any(d.get("id") == disc_id for d in m.get("discs") or []):
                return int(m.get("position") or 1), m, True
    for m in media:
        fmt = m.get("format")
        count = m.get("track-count") or len(m.get("tracks") or [])
        if count == track_count and (fmt is None or fmt in CD_FORMATS):
            return int(m.get("position") or 1), m, False
    return None


def parse_release(
    release: dict[str, Any], *, disc_id: str | None, track_count: int
) -> ReleaseMeta | None:
    picked = _pick_medium(release, disc_id, track_count)
    if picked is None:
        return None
    position, medium, exact = picked
    album_artist, album_ids = credit(release.get("artist-credit"))
    tracks: list[TrackMeta] = []
    for t in medium.get("tracks") or []:
        rec = t.get("recording") or {}
        name, ids = credit(t.get("artist-credit") or rec.get("artist-credit"))
        tracks.append(
            TrackMeta(
                number=int(t.get("position") or len(tracks) + 1),
                title=t.get("title") or rec.get("title") or "",
                artist=name or album_artist,
                artist_ids=ids or album_ids,
                isrcs=tuple(rec.get("isrcs") or ()),
                recording_id=rec.get("id"),
                track_id=t.get("id"),
                length_ms=t.get("length") or rec.get("length"),
            )
        )
    labels = release.get("label-info") or []
    label = next((li["label"]["name"] for li in labels if li.get("label")), None)
    catno = next((li["catalog-number"] for li in labels if li.get("catalog-number")), None)
    rg = release.get("release-group") or {}
    media = release.get("media") or []
    return ReleaseMeta(
        source=Source.MUSICBRAINZ,
        title=release.get("title") or "",
        artist=album_artist,
        artist_ids=album_ids,
        tracks=tracks,
        release_id=release.get("id"),
        release_group_id=rg.get("id"),
        disc_id=disc_id if exact else None,
        date=release.get("date") or None,
        original_date=rg.get("first-release-date") or None,
        country=release.get("country"),
        label=label,
        catalog_number=catno,
        barcode=release.get("barcode") or None,
        media_format=medium.get("format"),
        disc_number=position,
        disc_total=max(len(media), 1),
        status=release.get("status"),
        disambiguation=release.get("disambiguation") or None,
        exact=exact,
        cover_front=(release.get("cover-art-archive") or {}).get("front"),
    )


def parse_releases(
    data: dict[str, Any], *, disc_id: str | None, track_count: int
) -> list[ReleaseMeta]:
    out = []
    for r in data.get("releases") or []:
        meta = parse_release(r, disc_id=disc_id, track_count=track_count)
        if meta is not None:
            out.append(meta)
    # Exact disc-ID matches first; keep MusicBrainz's order otherwise.
    out.sort(key=lambda m: not m.exact)
    return out


class MusicBrainz:
    def __init__(self, web: Web) -> None:
        self.web = web

    def lookup(self, toc: Toc) -> list[ReleaseMeta]:
        """Releases for the disc: by disc ID, or by TOC if the ID is unknown."""
        disc_id = mb_disc_id(toc)
        count = len(toc.audio_tracks)
        data = self.web.get_json(f"{API}/discid/{quote(disc_id)}?inc={INC}&fmt=json")
        if data:
            found = parse_releases(data, disc_id=disc_id, track_count=count)
            if found:
                return found
        toc_s = mb_toc_string(toc).replace(" ", "+")
        data = self.web.get_json(f"{API}/discid/-?toc={toc_s}&inc={INC}&fmt=json")
        return parse_releases(data or {}, disc_id=disc_id, track_count=count)


def preselect(releases: list[ReleaseMeta], mcn: str | None) -> int | None:
    """Index of the release to preselect: the one whose barcode matches the disc's
    MCN, else the only candidate, else the only exact disc-ID match. None means the
    user should choose."""
    if not releases:
        return None
    if mcn:
        hits = [i for i, r in enumerate(releases) if same_barcode(r.barcode, mcn)]
        if len(hits) == 1:
            return hits[0]
    if len(releases) == 1:
        return 0
    exact = [i for i, r in enumerate(releases) if r.exact]
    return exact[0] if len(exact) == 1 else None
