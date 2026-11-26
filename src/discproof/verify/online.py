"""Network access for verification: AccurateRip disc lookups and the drive offset list.

Both go through one :class:`httpx.Client` (injectable, so tests use recorded
fixtures via ``httpx.MockTransport``) and are cached on disk.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from discproof import __version__
from discproof.config import paths
from discproof.verify.accuraterip import ArDiscId, ArResponse, parse_ar_bin
from discproof.verify.offsets import (
    DRIVE_OFFSETS_URL,
    DriveOffsetEntry,
    parse_drive_offsets,
)

USER_AGENT = f"DiscProof/{__version__}"
DRIVE_LIST_MAX_AGE_S = 30 * 24 * 3600
TIMEOUT_S = 20.0


class OnlineError(Exception):
    """The service could not be reached or answered with an error."""


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
        follow_redirects=True,
        transport=transport,
    )


class AccurateRipClient:
    def __init__(self, client: httpx.Client | None = None, cache: Path | None = None) -> None:
        self.client = client or make_client()
        self.cache = cache if cache is not None else paths.cache_dir() / "accuraterip"

    def lookup(self, disc: ArDiscId, *, refresh: bool = False) -> list[ArResponse] | None:
        """All responses for the disc, or None if the database does not know it.
        A cached answer is used unless ``refresh``."""
        cached = self.cache / disc.filename
        if cached.exists() and not refresh:
            return parse_ar_bin(cached.read_bytes())
        try:
            r = self.client.get(disc.url)
        except httpx.HTTPError as exc:
            raise OnlineError(f"AccurateRip unreachable: {exc}") from exc
        if r.status_code == 404 or (r.status_code == 200 and not r.content):
            return None
        if r.status_code != 200:
            raise OnlineError(f"AccurateRip answered HTTP {r.status_code}")
        responses = parse_ar_bin(r.content)
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(cached)
        return responses


def drive_offset_list(
    client: httpx.Client | None = None,
    path: Path | None = None,
    *,
    max_age_s: float = DRIVE_LIST_MAX_AGE_S,
    now: float | None = None,
) -> list[DriveOffsetEntry]:
    """The AccurateRip drive offset list, downloaded at most every ``max_age_s``.
    A stale copy is used if the download fails; with no copy at all the error
    propagates."""
    path = path if path is not None else paths.data_dir() / "driveoffsets.htm"
    now = time.time() if now is None else now
    fresh = path.exists() and now - path.stat().st_mtime < max_age_s
    if not fresh:
        try:
            r = (client or make_client()).get(DRIVE_OFFSETS_URL)
            r.raise_for_status()
            entries = parse_drive_offsets(r.text)
            if not entries:
                raise OnlineError("drive offset list is empty or its format changed")
        except (httpx.HTTPError, OnlineError) as exc:
            if not path.exists():
                raise OnlineError(f"cannot download the drive offset list: {exc}") from exc
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(r.text, encoding="utf-8")
            tmp.replace(path)
            return entries
    return parse_drive_offsets(path.read_text(encoding="utf-8", errors="replace"))
