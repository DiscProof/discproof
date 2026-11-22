"""Serve recorded fixtures for metadata tests (no network)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image

from perfect_audio_copy.drive.toc import Toc, Track
from perfect_audio_copy.metadata.web import ResponseCache, Web

FIXTURES = Path(__file__).parent / "fixtures"
DISC_ID = "49HHV7Eb8UKF3aQiNmu1GR8vKTY-"
RELEASE = "d3dc4be9-9749-4959-99e5-133d0cb467fe"

# The MusicBrainz documentation example disc ("Ettella Diamant").
EXAMPLE_TOC = Toc(
    tuple(Track(i + 1, o - 150, 0) for i, o in enumerate([150, 15363, 32314, 46592, 63414, 80489])),
    ((1, 95462 - 150),),
)


def png(width: int, height: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height), (200, 30, 60) if mode == "RGB" else None).save(buf, "PNG")
    return buf.getvalue()


Route = Callable[[httpx.Request], httpx.Response | None]


def fixture(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES / name).read_bytes())


def default_routes(request: httpx.Request) -> httpx.Response | None:
    url = str(request.url)
    if url.startswith(f"https://musicbrainz.org/ws/2/discid/{DISC_ID}?"):
        return fixture(f"mb_discid_{DISC_ID}.json")
    if url.startswith("https://musicbrainz.org/ws/2/discid/-?toc=1+6+95462+"):
        return fixture("mb_toc_1_6_95462.json")
    if url.startswith("https://musicbrainz.org/ws/2/discid/"):
        return httpx.Response(404, content=(FIXTURES / "mb_notfound.json").read_bytes())
    if url == f"https://coverartarchive.org/release/{RELEASE}":
        return fixture("caa_release_d3dc4be9.json")
    if url.startswith("https://coverartarchive.org/release/") and url.endswith(
        "/42371008619-1200.jpg"
    ):
        return httpx.Response(200, content=png(1200, 1200))
    if (
        url.startswith("https://api.discogs.com/database/search?")
        and "barcode=8586010200415" in url
    ):
        return fixture("discogs_search_barcode_8586010200415.json")
    if url == "https://api.discogs.com/releases/2803893":
        return fixture("discogs_release_2803893.json")
    return None


class MockWeb:
    """A Web whose requests are answered by routes (first non-None wins)."""

    def __init__(self, *routes: Route, cache: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.routes = [*routes, default_routes]
        self.slept: list[float] = []
        self.now = 0.0
        transport = httpx.MockTransport(self._handle)
        self.web = Web(
            httpx.Client(transport=transport, headers={"User-Agent": "test"}),
            ResponseCache(Path(":memory:")) if cache else None,
            clock=lambda: self.now,
            sleep=self._sleep,
        )

    def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for route in self.routes:
            r = route(request)
            if r is not None:
                return r
        return httpx.Response(404)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]
