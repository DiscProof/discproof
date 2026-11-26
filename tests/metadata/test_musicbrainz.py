"""MusicBrainz parsing and lookup against recorded WS/2 responses."""

from __future__ import annotations

import json

from mockweb import DISC_ID, EXAMPLE_TOC, FIXTURES, RELEASE, MockWeb

from discproof.metadata.model import ReleaseMeta, Source, same_barcode
from discproof.metadata.musicbrainz import MusicBrainz, parse_releases, preselect


def load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())


def test_parse_disc_id_response() -> None:
    [r] = parse_releases(load(f"mb_discid_{DISC_ID}.json"), disc_id=DISC_ID, track_count=6)
    assert r.source is Source.MUSICBRAINZ and r.exact and r.disc_id == DISC_ID
    assert (r.title, r.artist) == ("Ettella Diamant", "Ettella Diamant")
    assert r.release_id == RELEASE
    assert r.release_group_id == "1e18709f-ae50-39de-aef5-3cce3229791b"
    assert (r.country, r.date, r.original_date) == ("SK", "2004", "2004")
    assert (r.label, r.catalog_number, r.barcode) == ("Deadred Records", "DR E006", "8586010200415")
    assert (r.media_format, r.disc_number, r.disc_total) == ("CD", 1, 1)
    assert r.cover_front is True
    assert [t.number for t in r.tracks] == [1, 2, 3, 4, 5, 6]
    assert [t.title for t in r.tracks][:2] == ["Rysperdal Gstp", "Nicotine"]
    assert all(t.recording_id and t.track_id and t.artist == "Ettella Diamant" for t in r.tracks)
    assert r.artist_ids and r.tracks[0].artist_ids == r.artist_ids
    assert r.summary() == "SK 2004 · Deadred Records DR E006 · 8586010200415 · CD"


def test_parse_fuzzy_toc_response_puts_exact_match_first() -> None:
    found = parse_releases(load("mb_toc_1_6_95462.json"), disc_id=DISC_ID, track_count=6)
    assert [r.title for r in found] == ["Ettella Diamant", "Awakening"]
    assert found[0].exact and not found[1].exact


def test_wrong_track_count_is_skipped() -> None:
    assert parse_releases(load(f"mb_discid_{DISC_ID}.json"), disc_id=None, track_count=9) == []


def test_multi_disc_picks_medium_with_disc_id() -> None:
    data = load(f"mb_discid_{DISC_ID}.json")
    release = data["releases"][0]
    disc1 = json.loads(json.dumps(release["media"][0]))
    disc1["discs"] = [{"id": "other-disc-id"}]
    release["media"][0]["position"] = 2
    release["media"] = [disc1, release["media"][0]]
    disc1["position"] = 1
    [r] = parse_releases(data, disc_id=DISC_ID, track_count=6)
    assert (r.disc_number, r.disc_total, r.exact) == (2, 2, True)


def test_lookup_by_disc_id_makes_one_request() -> None:
    mock = MockWeb()
    found = MusicBrainz(mock.web).lookup(EXAMPLE_TOC)
    assert [r.release_id for r in found] == [RELEASE]
    [url] = mock.urls()
    assert url == (
        f"https://musicbrainz.org/ws/2/discid/{DISC_ID}"
        "?inc=artists+recordings+release-groups+labels+isrcs&fmt=json"
    )


def test_lookup_falls_back_to_toc() -> None:
    def unknown_id(request):  # type: ignore[no-untyped-def]
        import httpx

        if f"/discid/{DISC_ID}" in str(request.url):
            return httpx.Response(404, content=b'{"error":"Not Found"}')
        return None

    mock = MockWeb(unknown_id)
    found = MusicBrainz(mock.web).lookup(EXAMPLE_TOC)
    assert len(found) == 2
    assert "/discid/-?toc=1+6+95462+150+15363+32314+46592+63414+80489&inc=" in mock.urls()[1]


def meta(barcode: str | None, exact: bool = False) -> ReleaseMeta:
    return ReleaseMeta(Source.MUSICBRAINZ, "t", "a", [], barcode=barcode, exact=exact)


def test_preselect() -> None:
    assert preselect([], None) is None
    assert preselect([meta("1")], None) == 0
    two = [meta("0724384260927"), meta("724384260927")]
    assert preselect(two, None) is None  # user must choose
    assert preselect([meta("111"), meta("724384260927")], "0724384260927") == 1  # MCN = barcode
    assert preselect([meta("111", exact=True), meta("222")], None) == 0


def test_upc_and_ean_compare_equal() -> None:
    assert same_barcode("724384260927", "0724384260927")
    assert not same_barcode("724384260927", None)
    assert not same_barcode("", "")
