"""Discogs parsing and search against recorded responses."""

from __future__ import annotations

import json

import httpx
from mockweb import FIXTURES, MockWeb

from discproof.metadata.discogs import Discogs, parse_release
from discproof.metadata.model import Source


def release_json() -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / "discogs_release_2803893.json").read_text())


def test_parse_release() -> None:
    r = parse_release(release_json(), track_count=6)
    assert r is not None and r.source is Source.DISCOGS
    assert (r.title, r.artist, r.release_id) == ("Ettella Diamant", "Ettella Diamant", "2803893")
    assert (r.country, r.date, r.label, r.catalog_number) == (
        "Slovakia",
        "2004",
        "Deadred Records",
        "DR E006",
    )
    assert r.media_format == "CD" and r.barcode == "8586010200415"
    assert [t.title for t in r.tracks] == [
        "Rysperdal Gstp",
        "Nicotine",
        "Genius",
        "Twilight",
        "Dunkelheit",
        "Smog",
    ]
    assert r.tracks[0].length_ms == 202_000
    assert r.image_urls and r.genre


def test_wrong_track_count_is_rejected() -> None:
    assert parse_release(release_json(), track_count=7) is None


def test_multi_disc_artists_and_dates() -> None:
    data = {
        "id": 1,
        "title": "Box",
        "artists": [
            {"name": "Foo (2)", "anv": "", "join": "&"},
            {"name": "Bar", "anv": "The Bar", "join": ""},
        ],
        "released": "1999-03-00",
        "labels": [{"name": "Label (3)", "catno": "X1"}],
        "tracklist": [
            {"position": "1-1", "type_": "track", "title": "a", "duration": "1:00"},
            {"position": "", "type_": "heading", "title": "Disc 2"},
            {"position": "2-1", "type_": "track", "title": "b", "duration": "1:02:03"},
            {"position": "2-2", "type_": "track", "title": "c", "duration": ""},
        ],
    }
    r = parse_release(data, track_count=2)
    assert r is not None
    assert (r.artist, r.label, r.date) == ("Foo & The Bar", "Label", "1999-03")
    assert (r.disc_number, r.disc_total) == (2, 2)
    assert [t.title for t in r.tracks] == ["b", "c"]
    assert r.tracks[0].length_ms == 3_723_000 and r.tracks[1].length_ms is None


def test_find_by_barcode_without_token() -> None:
    mock = MockWeb()
    r = Discogs(mock.web).find(track_count=6, barcode="8586010200415")
    assert r is not None and r.release_id == "2803893"
    assert all("Authorization" not in req.headers for req in mock.requests)


def test_token_is_sent() -> None:
    mock = MockWeb()
    Discogs(mock.web, token="secret").find(track_count=6, barcode="8586010200415")
    assert mock.requests[0].headers["Authorization"] == "Discogs token=secret"


def test_find_by_artist_and_title() -> None:
    def search(request: httpx.Request) -> httpx.Response | None:
        q = request.url.params
        if "database/search" in str(request.url) and q.get("artist") == "Ettella Diamant":
            return httpx.Response(200, json={"results": [{"id": 2803893, "format": ["CD"]}]})
        return None

    mock = MockWeb(search)
    r = Discogs(mock.web).find(track_count=6, artist="Ettella Diamant", title="Ettella Diamant")
    assert r is not None and r.release_id == "2803893"
