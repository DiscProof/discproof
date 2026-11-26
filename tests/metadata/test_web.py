"""Rate limiting, caching and error handling of the metadata HTTP layer."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mockweb import MockWeb

from discproof import PROJECT_URL
from discproof.metadata.web import (
    NOT_FOUND_MAX_AGE,
    USER_AGENT,
    RateLimiter,
    ResponseCache,
    Web,
    WebError,
)


def test_user_agent_names_app_and_contact_but_no_email() -> None:
    assert USER_AGENT.startswith("DiscProof/")
    assert USER_AGENT.endswith(f"( {PROJECT_URL} )")
    assert "@" not in USER_AGENT


def test_rate_limiter_spaces_requests() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    lim = RateLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    lim.wait()
    lim.wait()
    now[0] += 0.25
    lim.wait()
    assert slept == [1.0, 0.75]


def test_musicbrainz_is_limited_to_one_request_per_second() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    web = Web(client, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        web.get_json("https://musicbrainz.org/ws/2/x")
    assert slept == [1.0, 1.0]
    web.get_json("https://coverartarchive.org/release/x")  # other hosts are not limited here
    assert slept == [1.0, 1.0]


def test_cache_serves_repeat_lookups(tmp_path: Path) -> None:
    mock = MockWeb(lambda r: httpx.Response(200, json={"ok": 1}), cache=True)
    assert mock.web.get_json("https://musicbrainz.org/ws/2/a") == {"ok": 1}
    assert mock.web.get_json("https://musicbrainz.org/ws/2/a") == {"ok": 1}
    assert len(mock.requests) == 1


def test_not_found_is_cached_briefly(tmp_path: Path) -> None:
    now = [0.0]
    cache = ResponseCache(tmp_path / "m.sqlite", clock=lambda: now[0])
    calls = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(r)
        return httpx.Response(404)

    web = Web(httpx.Client(transport=httpx.MockTransport(handler)), cache, sleep=lambda s: None)
    assert web.get_json("https://musicbrainz.org/ws/2/missing") is None
    assert web.get_json("https://musicbrainz.org/ws/2/missing") is None
    assert len(calls) == 1
    now[0] += NOT_FOUND_MAX_AGE + 1
    web.get_json("https://musicbrainz.org/ws/2/missing")
    assert len(calls) == 2


def test_cache_persists_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "m.sqlite"
    ResponseCache(path).put("k", 200, b"body")
    hit = ResponseCache(path).get("k")
    assert hit is not None and hit[:2] == (200, b"body")


def test_503_is_retried_with_backoff() -> None:
    answers = [httpx.Response(503), httpx.Response(503), httpx.Response(200, json=[1])]
    mock = MockWeb(lambda r: answers.pop(0))
    assert mock.web.get_json("https://musicbrainz.org/ws/2/x") == [1]
    assert len(mock.requests) == 3
    # Backoff sleeps of 2 s and 4 s cover the 1 s MusicBrainz spacing, so no extra waits.
    assert mock.slept == [2.0, 4.0]


def test_errors() -> None:
    with pytest.raises(WebError):
        MockWeb(lambda r: httpx.Response(500)).web.get_json("https://musicbrainz.org/ws/2/x")
    with pytest.raises(WebError):
        MockWeb(lambda r: httpx.Response(200, content=b"<html>")).web.get_json("https://x.org/y")

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=r)

    with pytest.raises(WebError):
        MockWeb(boom).web.get_json("https://musicbrainz.org/ws/2/x")
