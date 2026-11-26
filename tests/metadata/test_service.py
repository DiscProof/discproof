"""The lookup chain and cover selection, end to end over recorded responses."""

from __future__ import annotations

import httpx
from mockweb import DISC_ID, EXAMPLE_TOC, RELEASE, MockWeb, png

from discproof.drive.cdtext import CdText, CdTextEntry
from discproof.drive.device import DiscInfo
from discproof.metadata.model import ReleaseMeta, Source, TrackMeta
from discproof.metadata.service import MetadataService


def service(*routes, **kw) -> tuple[MetadataService, MockWeb]:  # type: ignore[no-untyped-def]
    mock = MockWeb(*routes)
    return MetadataService(mock.web, **kw), mock


def mb_down(request: httpx.Request) -> httpx.Response | None:
    return httpx.Response(500) if request.url.host == "musicbrainz.org" else None


def mb_unknown(request: httpx.Request) -> httpx.Response | None:
    return httpx.Response(404) if request.url.host == "musicbrainz.org" else None


def cdtext() -> CdText:
    return CdText(
        album=CdTextEntry(title="Ettella Diamant", performer="Ettella Diamant"),
        tracks={
            1: CdTextEntry(title="Rysperdal Gstp"),
            2: CdTextEntry(title="Nicotine", performer="Guest"),
        },
    )


def test_exact_musicbrainz_match_is_preselected() -> None:
    svc, mock = service()
    disc = DiscInfo(EXAMPLE_TOC, isrcs={2: "SKA010400002"})
    res = svc.lookup(disc)
    assert res.disc_id == DISC_ID and not res.errors
    assert [r.release_id for r in res.releases] == [RELEASE]
    assert res.preselected == 0 and res.unambiguous
    assert res.releases[0].tracks[1].isrcs[0] == "SKA010400002"  # disc ISRC first
    assert all(u.startswith("https://musicbrainz.org/") for u in mock.urls())  # no Discogs


def test_fuzzy_matches_need_a_choice_unless_mcn_decides() -> None:
    def only_toc(request: httpx.Request) -> httpx.Response | None:
        if f"/discid/{DISC_ID}" in str(request.url):
            return httpx.Response(404)
        return None

    svc, _ = service(only_toc)
    res = svc.lookup(DiscInfo(EXAMPLE_TOC))
    assert len(res.releases) == 2
    # The listed disc ID makes one of them exact even though it came from the TOC search.
    assert res.preselected == 0 and res.releases[0].exact
    res = svc.lookup(DiscInfo(EXAMPLE_TOC, mcn="0858601020041"))  # no barcode matches
    assert res.preselected == 0


def test_musicbrainz_down_falls_back_to_discogs_by_mcn() -> None:
    svc, mock = service(mb_down)
    res = svc.lookup(DiscInfo(EXAMPLE_TOC, mcn="8586010200415"))
    assert res.errors and res.errors[0].startswith("MusicBrainz:")
    assert [r.source for r in res.releases] == [Source.DISCOGS]
    assert res.preselected == 0 and res.unambiguous
    assert any("api.discogs.com" in u for u in mock.urls())


def test_unknown_everywhere_uses_cdtext() -> None:
    svc, _ = service(mb_unknown, use_discogs=False)
    res = svc.lookup(DiscInfo(EXAMPLE_TOC, cdtext=cdtext()))
    [r] = res.releases
    assert r.source is Source.CDTEXT and res.preselected == 0 and not res.unambiguous
    assert (r.title, r.artist) == ("Ettella Diamant", "Ettella Diamant")
    assert [t.title for t in r.tracks][:2] == ["Rysperdal Gstp", "Nicotine"]
    assert [t.artist for t in r.tracks][:2] == ["Ettella Diamant", "Guest"]
    assert len(r.tracks) == 6 and r.tracks[5].title == ""


def test_nothing_known_gives_editable_blank() -> None:
    svc, _ = service(mb_unknown, use_discogs=False)
    [r] = svc.lookup(DiscInfo(EXAMPLE_TOC)).releases
    assert r.source is Source.MANUAL
    assert [t.title for t in r.tracks] == [f"Track {n:02d}" for n in range(1, 7)]


def test_cover_from_cover_art_archive() -> None:
    svc, _ = service()
    release = svc.lookup(DiscInfo(EXAMPLE_TOC)).releases[0]
    cover = svc.cover(release)
    assert cover is not None and (cover.width, cover.embed_width) == (1200, 1000)
    assert cover.source_url and cover.source_url.endswith("-1200.jpg")


def test_cover_falls_back_to_discogs_images() -> None:
    def no_caa(request: httpx.Request) -> httpx.Response | None:
        if request.url.host == "coverartarchive.org":
            return httpx.Response(404)
        if request.url.host == "i.discogs.com":
            return httpx.Response(200, content=png(600, 600))
        return None

    svc, _ = service(no_caa)
    release = svc.lookup(DiscInfo(EXAMPLE_TOC)).releases[0]
    cover = svc.cover(release)
    assert cover is not None and cover.width == 600
    assert cover.source_url and "discogs" in cover.source_url


def test_no_cover_anywhere() -> None:
    svc, _ = service(lambda r: httpx.Response(404))
    manual = ReleaseMeta(Source.MANUAL, "x", "y", [TrackMeta(1, "a", "b")])
    assert svc.cover(manual) is None
