"""FLAC encoding with the reference encoder, in parallel with extraction.

Each job pipes the track's PCM into ``flac --verify`` (the encoder decodes what it
wrote and compares), writes tags and the cover into the temporary file, and only
then renames it into place, so a crash never leaves a half-written track under its
final name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from perfect_audio_copy.metadata.coverart import Cover
from perfect_audio_copy.output.tags import Tags, write_tags

RAW_FORMAT = [
    "--force-raw-format",
    "--endian=little",
    "--sign=signed",
    "--channels=2",
    "--bps=16",
    "--sample-rate=44100",
]
PARTIAL_SUFFIX = ".partial.flac"


class EncodeError(RuntimeError):
    pass


def flac_binary() -> str:
    path = shutil.which("flac")
    if path is None:
        raise EncodeError("the 'flac' program is not installed (dnf install flac)")
    return path


@dataclass(frozen=True, slots=True)
class EncodeJob:
    number: int
    pcm: bytes
    dest: Path
    tags: Tags
    cover: Cover | None = None


@dataclass(frozen=True, slots=True)
class Encoded:
    number: int
    path: Path
    size: int


def encode(job: EncodeJob, *, level: int = 8, flac: str | None = None) -> Encoded:
    """Encode one track synchronously."""
    if not 0 <= level <= 8:
        raise EncodeError(f"FLAC compression level must be 0..8, not {level}")
    job.dest.parent.mkdir(parents=True, exist_ok=True)
    partial = job.dest.with_name(job.dest.name + PARTIAL_SUFFIX)
    cmd = [
        flac or flac_binary(),
        f"-{level}",
        "--verify",
        "--silent",
        "--force",
        *RAW_FORMAT,
        "-o",
        str(partial),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, input=job.pcm, capture_output=True, check=False)
        if proc.returncode != 0:
            msg = proc.stderr.decode("utf-8", "replace").strip()
            raise EncodeError(f"flac failed for track {job.number}: {msg or proc.returncode}")
        write_tags(partial, job.tags, job.cover)
        os.replace(partial, job.dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return Encoded(job.number, job.dest, job.dest.stat().st_size)


class EncoderPool:
    """Runs encode jobs on worker threads (each one a ``flac`` process)."""

    def __init__(
        self,
        workers: int | None = None,
        *,
        level: int = 8,
        on_done: Callable[[Encoded | None, EncodeJob, BaseException | None], None] | None = None,
    ) -> None:
        self.level = level
        self.flac = flac_binary()
        self._pool = ThreadPoolExecutor(
            max_workers=workers or max(1, (os.cpu_count() or 2) - 1),
            thread_name_prefix="flac",
        )
        self._futures: list[tuple[EncodeJob, Future[Encoded]]] = []
        self._on_done = on_done

    def submit(self, job: EncodeJob) -> Future[Encoded]:
        fut = self._pool.submit(encode, job, level=self.level, flac=self.flac)
        if self._on_done is not None:
            cb = self._on_done

            def done(f: Future[Encoded]) -> None:
                exc = f.exception()
                cb(None if exc else f.result(), job, exc)

            fut.add_done_callback(done)
        self._futures.append((job, fut))
        return fut

    def wait(self) -> tuple[list[Encoded], list[tuple[int, BaseException]]]:
        """Block until every job finished: (successes, (track, error) failures)."""
        ok: list[Encoded] = []
        failed: list[tuple[int, BaseException]] = []
        for job, fut in self._futures:
            exc = fut.exception()
            if exc is None:
                ok.append(fut.result())
            else:
                failed.append((job.number, exc))
        ok.sort(key=lambda e: e.number)
        return ok, failed

    def shutdown(self, *, cancel: bool = False) -> None:
        self._pool.shutdown(wait=True, cancel_futures=cancel)


def add_replaygain(paths: list[Path]) -> None:
    """Album + track ReplayGain via ``metaflac --add-replay-gain`` (one album)."""
    if not paths:
        return
    metaflac = shutil.which("metaflac")
    if metaflac is None:
        raise EncodeError("the 'metaflac' program is not installed (dnf install flac)")
    proc = subprocess.run(
        [metaflac, "--add-replay-gain", *map(str, paths)], capture_output=True, check=False
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise EncodeError(f"metaflac --add-replay-gain failed: {msg or proc.returncode}")
