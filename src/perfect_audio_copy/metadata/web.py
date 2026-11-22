"""HTTP for the metadata sources: per-host rate limits, retries on 503, and a
SQLite response cache (``metadata.sqlite`` in the data directory)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from perfect_audio_copy import PROJECT_URL, __version__
from perfect_audio_copy.config import paths

USER_AGENT = f"PerfectAudioCopy/{__version__} ( {PROJECT_URL} )"
TIMEOUT_S = 20.0
#: Seconds between requests per host. MusicBrainz asks for at most 1/s; Discogs
#: allows 60/min with a token and 25/min without.
HOST_INTERVALS = {
    "musicbrainz.org": 1.0,
    "api.discogs.com": 2.5,
}
DAY = 24 * 3600.0
#: Cache lifetimes: found data changes rarely; "not found" may be fixed by an editor.
FOUND_MAX_AGE = 30 * DAY
NOT_FOUND_MAX_AGE = 1 * DAY


class WebError(Exception):
    """A service could not be reached or answered with an unexpected error."""


class RateLimiter:
    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


class ResponseCache:
    """URL -> (status, body). Thread-safe; one connection guarded by a lock."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path if path is not None else paths.data_dir() / "metadata.sqlite"
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS http ("
            " key TEXT PRIMARY KEY, fetched REAL NOT NULL, status INTEGER NOT NULL, body BLOB)"
        )
        self._db.commit()
        self._lock = threading.Lock()
        self._clock = clock

    def get(self, key: str) -> tuple[int, bytes, float] | None:
        """(status, body, age in seconds), or None."""
        with self._lock:
            row = self._db.execute(
                "SELECT fetched, status, body FROM http WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return int(row[1]), bytes(row[2] or b""), self._clock() - float(row[0])

    def put(self, key: str, status: int, body: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO http (key, fetched, status, body) VALUES (?, ?, ?, ?)",
                (key, self._clock(), status, body),
            )
            self._db.commit()

    def close(self) -> None:
        self._db.close()


class Web:
    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: ResponseCache | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True
        )
        self.cache = cache
        self._limiters = {
            h: RateLimiter(i, clock=clock, sleep=sleep) for h, i in HOST_INTERVALS.items()
        }
        self._sleep = sleep
        self.retries = retries
        self.requests = 0

    def _limit(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        for h, limiter in self._limiters.items():
            if host == h or host.endswith("." + h):
                limiter.wait()

    def get(
        self, url: str, *, headers: dict[str, str] | None = None, cache: bool = True
    ) -> tuple[int, bytes]:
        """(status, body); 404 is a normal answer. Cached unless ``cache=False``."""
        if cache and self.cache is not None:
            hit = self.cache.get(url)
            if hit is not None:
                status, body, age = hit
                if age <= (FOUND_MAX_AGE if status == 200 else NOT_FOUND_MAX_AGE):
                    return status, body
        for attempt in range(self.retries + 1):
            self._limit(url)
            try:
                self.requests += 1
                r = self.client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise WebError(f"{urlsplit(url).hostname}: {exc}") from exc
            if r.status_code in (429, 503) and attempt < self.retries:
                self._sleep(2.0 * (attempt + 1))  # rate limited: back off and retry
                continue
            break
        if r.status_code not in (200, 404):
            raise WebError(f"{urlsplit(url).hostname} answered HTTP {r.status_code}")
        if cache and self.cache is not None:
            self.cache.put(url, r.status_code, r.content)
        return r.status_code, r.content

    def get_json(
        self, url: str, *, headers: dict[str, str] | None = None, cache: bool = True
    ) -> Any | None:
        """Parsed JSON, or None for 404."""
        status, body = self.get(url, headers=headers, cache=cache)
        if status == 404:
            return None
        try:
            return json.loads(body)
        except ValueError as exc:
            raise WebError(f"{urlsplit(url).hostname} returned invalid JSON") from exc

    def get_bytes(self, url: str) -> bytes | None:
        """Binary content (images; not cached). None for 404."""
        status, body = self.get(url, cache=False)
        return None if status == 404 else body
