"""AccurateRip and drive-list clients against recorded responses (no network)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from discproof.verify.accuraterip import ArDiscId, ArEntry, ArResponse, build_ar_bin
from discproof.verify.online import AccurateRipClient, OnlineError, drive_offset_list

FIXTURE = Path(__file__).parent / "fixtures" / "driveoffsets.htm"
DISC = ArDiscId(2, 0xF21C, 0x27EF8, 0x05021002)
BIN = build_ar_bin(
    [
        ArResponse(
            2,
            DISC.id1,
            DISC.id2,
            DISC.cddb,
            (ArEntry(12, 0x284FC705, 0), ArEntry(20, 0x9CC1F32E, 0)),
        )
    ]
)


def client(handler, calls: list[str]) -> httpx.Client:  # type: ignore[no-untyped-def]
    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["User-Agent"].startswith("DiscProof/")
        return handler(request)

    return httpx.Client(
        transport=httpx.MockTransport(wrapped), headers={"User-Agent": "DiscProof/test"}
    )


def test_lookup_parses_and_caches(tmp_path: Path) -> None:
    calls: list[str] = []
    ar = AccurateRipClient(client(lambda r: httpx.Response(200, content=BIN), calls), tmp_path)
    got = ar.lookup(DISC)
    assert got is not None and got[0].tracks[1].confidence == 20
    assert calls == [
        "http://www.accuraterip.com/accuraterip/c/1/2/dBAR-002-0000f21c-00027ef8-05021002.bin"
    ]
    assert ar.lookup(DISC) == got
    assert len(calls) == 1  # served from cache
    ar.lookup(DISC, refresh=True)
    assert len(calls) == 2


def test_lookup_not_in_database(tmp_path: Path) -> None:
    calls: list[str] = []
    ar = AccurateRipClient(client(lambda r: httpx.Response(404), calls), tmp_path)
    assert ar.lookup(DISC) is None
    assert not any(tmp_path.iterdir())  # a miss is not cached


def test_lookup_server_error(tmp_path: Path) -> None:
    ar = AccurateRipClient(client(lambda r: httpx.Response(503), []), tmp_path)
    with pytest.raises(OnlineError):
        ar.lookup(DISC)


def test_lookup_network_error(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    ar = AccurateRipClient(client(boom, []), tmp_path)
    with pytest.raises(OnlineError):
        ar.lookup(DISC)


def test_drive_list_download_and_cache(tmp_path: Path) -> None:
    calls: list[str] = []
    html = FIXTURE.read_text()
    c = client(lambda r: httpx.Response(200, text=html), calls)
    path = tmp_path / "driveoffsets.htm"
    entries = drive_offset_list(c, path)
    assert len(entries) == 8 and path.exists()
    assert calls == ["http://www.accuraterip.com/driveoffsets.htm"]
    drive_offset_list(c, path)
    assert len(calls) == 1  # fresh copy reused


def test_drive_list_stale_copy_used_when_offline(tmp_path: Path) -> None:
    path = tmp_path / "driveoffsets.htm"
    path.write_text(FIXTURE.read_text())
    os.utime(path, (0, 0))  # very old

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    assert len(drive_offset_list(client(boom, []), path)) == 8


def test_drive_list_unavailable(tmp_path: Path) -> None:
    with pytest.raises(OnlineError):
        drive_offset_list(client(lambda r: httpx.Response(500), []), tmp_path / "x.htm")
    with pytest.raises(OnlineError):  # page format changed: no rows
        drive_offset_list(
            client(lambda r: httpx.Response(200, text="<html></html>"), []), tmp_path / "y.htm"
        )
