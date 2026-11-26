"""Discogs: fallback metadata and cover images.

Search works without a token at a lower rate; a personal token from Settings raises
the limit (and is sent as ``Authorization: Discogs token=...``).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from discproof.metadata.model import ReleaseMeta, Source, TrackMeta, same_barcode
from discproof.metadata.web import Web

API = "https://api.discogs.com"
MAX_CANDIDATES = 5
_NUMBERED = re.compile(r"\s+\(\d+\)$")  # "Artist (2)" disambiguation suffix
_POSITION = re.compile(r"^(?:CD|Disc|D)?\s*(\d+)[-.](\d+)$", re.IGNORECASE)


def _artist(entries: list[dict[str, Any]] | None) -> str:
    if not entries:
        return ""
    out = ""
    for e in entries:
        name = e.get("anv") or e.get("name") or ""
        out += _NUMBERED.sub("", name)
        join = (e.get("join") or "").strip()
        if join:
            out += " " + join + " " if join != "," else ", "
    return out.strip()


def _duration_ms(text: str | None) -> int | None:
    if not text:
        return None
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds * 1000


def _date(released: str | None, year: int | str | None) -> str | None:
    if released:
        parts = []
        for p in released.split("-"):
            if not p.isdigit() or int(p) == 0:
                break  # "1987-07-00": unknown day
            parts.append(p)
        if parts:
            return "-".join(parts)
    return str(year) if year else None


def _disc_of(position: str) -> tuple[int, int] | None:
    """('1-3' / 'CD2-1') -> (disc, track); plain '3' -> None."""
    m = _POSITION.match(position.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_release(data: dict[str, Any], *, track_count: int | None = None) -> ReleaseMeta | None:
    """Release JSON -> metadata for the disc with ``track_count`` tracks (any disc of a
    multi-disc set whose track count fits; the first if not given)."""
    album_artist = _artist(data.get("artists"))
    rows = [t for t in data.get("tracklist") or [] if (t.get("type_") or "track") == "track"]
    discs: dict[int, list[dict[str, Any]]] = {}
    for t in rows:
        d = _disc_of(t.get("position") or "")
        discs.setdefault(d[0] if d else 1, []).append(t)
    if track_count is None:
        disc_no = min(discs) if discs else 1
    else:
        fitting = [n for n, ts in sorted(discs.items()) if len(ts) == track_count]
        if not fitting:
            return None
        disc_no = fitting[0]
    tracks = [
        TrackMeta(
            number=i,
            title=t.get("title") or "",
            artist=_artist(t.get("artists")) or album_artist,
            length_ms=_duration_ms(t.get("duration")),
        )
        for i, t in enumerate(discs.get(disc_no, []), start=1)
    ]
    labels = data.get("labels") or []
    barcode = next(
        (
            "".join(c for c in i.get("value", "") if c.isdigit())
            for i in data.get("identifiers") or []
            if i.get("type") == "Barcode"
        ),
        None,
    )
    images = sorted(
        data.get("images") or [], key=lambda i: (i.get("type") != "primary", -(i.get("width") or 0))
    )
    formats = data.get("formats") or []
    genres = data.get("genres") or []
    return ReleaseMeta(
        source=Source.DISCOGS,
        title=data.get("title") or "",
        artist=album_artist,
        tracks=tracks,
        release_id=str(data["id"]) if data.get("id") is not None else None,
        date=_date(data.get("released"), data.get("year")),
        country=data.get("country"),
        label=_NUMBERED.sub("", labels[0]["name"]) if labels else None,
        catalog_number=labels[0].get("catno") if labels else None,
        barcode=barcode or None,
        media_format=formats[0].get("name") if formats else None,
        disc_number=disc_no,
        disc_total=max(len(discs), 1),
        genre=genres[0] if genres else None,
        image_urls=tuple(i["uri"] for i in images if i.get("uri")),
    )


class Discogs:
    def __init__(self, web: Web, token: str | None = None) -> None:
        self.web = web
        self.token = token or None

    def _headers(self) -> dict[str, str] | None:
        return {"Authorization": f"Discogs token={self.token}"} if self.token else None

    def _search(self, **params: str) -> list[int]:
        query = urlencode({"type": "release", **params})
        data = self.web.get_json(f"{API}/database/search?{query}", headers=self._headers())
        results = (data or {}).get("results") or []
        # CD releases first, otherwise keep Discogs' relevance order.
        results.sort(key=lambda r: "CD" not in (r.get("format") or []))
        return [int(r["id"]) for r in results if r.get("id") is not None]

    def release(self, release_id: int, *, track_count: int | None = None) -> ReleaseMeta | None:
        data = self.web.get_json(f"{API}/releases/{release_id}", headers=self._headers())
        return parse_release(data, track_count=track_count) if data else None

    def find(
        self,
        *,
        track_count: int,
        barcode: str | None = None,
        artist: str | None = None,
        title: str | None = None,
    ) -> ReleaseMeta | None:
        """Best release: by barcode first (must match), then by artist + title; the
        disc's track count must fit."""
        if barcode:
            for rid in self._search(barcode=barcode)[:MAX_CANDIDATES]:
                meta = self.release(rid, track_count=track_count)
                if meta and (meta.barcode is None or same_barcode(meta.barcode, barcode)):
                    return meta
        if artist and title:
            for rid in self._search(artist=artist, release_title=title)[:MAX_CANDIDATES]:
                meta = self.release(rid, track_count=track_count)
                if meta:
                    return meta
        return None
