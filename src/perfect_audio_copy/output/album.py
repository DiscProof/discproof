"""Writing one album: folder, FLAC files (encoded while the rip continues), AccurateRip
verification per track, ReplayGain, cover.jpg, CUE sheet and log.

Use::

    writer = AlbumWriter(settings, release, disc, ...)
    if writer.prepare() is None: ...            # folder exists and policy is SKIP
    result = Extractor(..., on_track=writer.on_track).run()
    output = writer.finish(result)
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from perfect_audio_copy import __version__
from perfect_audio_copy.drive.device import DiscInfo
from perfect_audio_copy.engine.events import RipResult, TrackResult
from perfect_audio_copy.engine.profile import DriveProfile, ExtractionSettings
from perfect_audio_copy.metadata.coverart import Cover
from perfect_audio_copy.metadata.discid import mb_disc_id
from perfect_audio_copy.metadata.model import ReleaseMeta, TrackMeta
from perfect_audio_copy.output.cue import render_cue
from perfect_audio_copy.output.encoder import (
    EncodeError,
    EncodeJob,
    EncoderPool,
    add_replaygain,
)
from perfect_audio_copy.output.log import render_log
from perfect_audio_copy.output.naming import DEFAULT_TEMPLATE, render, sanitize, track_fields
from perfect_audio_copy.output.report import AlbumReport, TrackReport
from perfect_audio_copy.output.tags import build_tags
from perfect_audio_copy.verify.accuraterip import ArResponse, TrackVerdict, cddb_disc_id
from perfect_audio_copy.verify.verify import IncrementalVerifier


class ExistingPolicy(Enum):
    """What to do when the album folder already exists (the UI asks the user)."""

    OVERWRITE = "overwrite"  # write into it, replacing files with the same names
    SUFFIX = "suffix"  # "Album (2004) (2)"
    SKIP = "skip"  # don't rip


@dataclass(frozen=True, slots=True)
class OutputSettings:
    library: Path
    template: str = DEFAULT_TEMPLATE
    flac_level: int = 8
    replaygain: bool = True
    existing: ExistingPolicy = ExistingPolicy.SUFFIX
    workers: int | None = None
    #: Multi-disc releases: one subfolder per disc ("Disc 1") instead of "1-01" names.
    disc_subfolders: bool = True


@dataclass(slots=True)
class AlbumOutput:
    folder: Path
    tracks: dict[int, Path]
    log: Path
    cue: Path | None
    cover: Path | None
    report: AlbumReport
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report.ok and not self.errors


def _track_meta(release: ReleaseMeta, number: int) -> TrackMeta:
    for t in release.tracks:
        if t.number == number:
            return t
    if number == 0:
        return TrackMeta(0, "Hidden Track", release.artist)
    return TrackMeta(number, f"Track {number:02d}", release.artist)


def disc_folder_name(settings: OutputSettings, release: ReleaseMeta) -> str | None:
    if settings.disc_subfolders and release.disc_total > 1:
        return f"Disc {release.disc_number}"
    return None


def track_name(settings: OutputSettings, release: ReleaseMeta, number: int) -> tuple[Path, str]:
    """(folder, file name) for a track; the folder includes "Disc N" when per-disc
    subfolders are used."""
    t = _track_meta(release, number)
    disc_folder = disc_folder_name(settings, release)
    name = render(
        settings.template,
        track_fields(
            albumartist=release.artist,
            album=release.title,
            date=release.date,
            title=t.title,
            artist=t.artist,
            tracknumber=number,
            tracktotal=len([x for x in release.tracks if x.number > 0]),
            discnumber=release.disc_number,
            disctotal=release.disc_total,
            genre=release.genre,
            label=release.label,
            catalognumber=release.catalog_number,
            disc_subfolders=disc_folder is not None,
        ),
        disc_folder=disc_folder,
    )
    return settings.library.joinpath(*name.folder), name.stem + ".flac"


def album_folder(settings: OutputSettings, release: ReleaseMeta) -> Path:
    return track_name(settings, release, 1)[0]


def folder_conflict(settings: OutputSettings, release: ReleaseMeta) -> Path | None:
    """The album folder if it already exists with files in it (ask the user)."""
    folder = album_folder(settings, release)
    return folder if folder.is_dir() and any(folder.iterdir()) else None


class AlbumWriter:
    def __init__(
        self,
        settings: OutputSettings,
        release: ReleaseMeta,
        disc: DiscInfo,
        *,
        drive: str,
        profile: DriveProfile,
        extraction: ExtractionSettings,
        cover: Cover | None = None,
        ar_responses: list[ArResponse] | None = None,
        ar_in_database: bool | None = None,
        ar_error: str | None = None,
        pregaps: dict[int, int] | None = None,
        gap_peaks: dict[int, int | None] | None = None,
        notes: list[str] | None = None,
        now: Callable[[], dt.datetime] = dt.datetime.now,
        on_encoded: Callable[[int, Path | None, str | None], None] | None = None,
    ) -> None:
        self.settings = settings
        self.release = release
        self.disc = disc
        self.drive = drive
        self.profile = profile
        self.extraction = extraction
        self.cover = cover
        self.ar_in_database = ar_in_database
        self.ar_error = ar_error
        self.pregaps = pregaps or {}
        self.gap_peaks = gap_peaks or {}
        self.notes = list(notes or [])
        self._now = now
        self._on_encoded = on_encoded
        self.disc_id = mb_disc_id(disc.toc)
        self.verifier = IncrementalVerifier(disc.toc, ar_responses if ar_in_database else None)
        self.verdicts: dict[int, TrackVerdict] = {}
        self.folder: Path | None = None
        self.files: dict[int, Path] = {}
        self.errors: dict[int, str] = {}
        self._lock = threading.Lock()
        self._pool: EncoderPool | None = None
        self._created = now()

    # ------------------------------------------------------------ setup

    def prepare(self) -> Path | None:
        """Choose the album folder. None if it exists and the policy is SKIP."""
        base = album_folder(self.settings, self.release)
        folder = base
        if folder.is_dir() and any(folder.iterdir()):
            policy = self.settings.existing
            if policy is ExistingPolicy.SKIP:
                return None
            if policy is ExistingPolicy.SUFFIX:
                n = 2
                while folder.exists():
                    folder = base.with_name(f"{base.name} ({n})")
                    n += 1
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self._pool = EncoderPool(
            self.settings.workers, level=self.settings.flac_level, on_done=self._encoded
        )
        return folder

    # ------------------------------------------------------------ per track

    def on_track(self, number: int, pcm: bytes) -> None:
        """Extractor ``on_track`` callback: verify, then queue the encode."""
        if self.folder is None or self._pool is None:
            raise RuntimeError("call prepare() before ripping")
        verdict = self.verifier.add(number, pcm)
        if verdict is not None:
            self.verdicts[number] = verdict
        meta = _track_meta(self.release, number)
        tags = build_tags(
            self.release,
            meta,
            disc_id=self.disc_id,
            ar_confidence=verdict.confidence if verdict and verdict.accurate else None,
            ar_in_database=self.ar_in_database,
        )
        _, filename = track_name(self.settings, self.release, number)
        dest = self.folder / filename
        with self._lock:
            self.files[number] = dest
        self._pool.submit(EncodeJob(number, pcm, dest, tags, self.cover))

    def _encoded(self, done: object, job: EncodeJob, exc: BaseException | None) -> None:
        if exc is not None:
            with self._lock:
                self.errors[job.number] = str(exc)
        if self._on_encoded is not None:
            self._on_encoded(job.number, None if exc else job.dest, str(exc) if exc else None)

    # ------------------------------------------------------------ finish

    def finish(self, rip: RipResult) -> AlbumOutput:
        """Wait for the encoders, then ReplayGain, cover.jpg, CUE and log."""
        if self.folder is None or self._pool is None:
            raise RuntimeError("call prepare() before finish()")
        self._pool.wait()
        self._pool.shutdown()
        errors: list[str] = []
        written = {n: p for n, p in self.files.items() if n not in self.errors and p.exists()}

        if self.settings.replaygain and written and not rip.cancelled:
            try:
                add_replaygain([written[n] for n in sorted(written)])
            except EncodeError as exc:
                errors.append(str(exc))

        cover_path: Path | None = None
        if self.cover is not None:
            cover_path = self.folder / "cover.jpg"
            _write_bytes(cover_path, self.cover.cover_jpeg)
            if disc_folder_name(self.settings, self.release) is not None:
                # Plex looks for multi-disc artwork in the album folder above "Disc N".
                album_cover = self.folder.parent / "cover.jpg"
                if not album_cover.exists():
                    _write_bytes(album_cover, self.cover.cover_jpeg)

        report = self._report(rip, written)
        stem = sanitize(self.release.title or "Album") or "Album"
        if self.release.disc_total > 1:
            stem += f" (Disc {self.release.disc_number})"
        cue_path: Path | None = None
        if not rip.cancelled and written:
            cue_path = self.folder / f"{stem}.cue"
            _write_text(cue_path, render_cue(report))
        log_path = self.folder / f"{stem}.log"
        _write_text(log_path, render_log(report))
        errors += [f"track {n}: {e}" for n, e in sorted(self.errors.items())]
        return AlbumOutput(self.folder, written, log_path, cue_path, cover_path, report, errors)

    def _report(self, rip: RipResult, written: dict[int, Path]) -> AlbumReport:
        ar = self.verifier.finish()
        toc = self.disc.toc
        by_number = {t.number: t for t in rip.tracks}
        tracks: list[TrackReport] = []
        numbers = sorted(set(by_number) | set(self.files))
        for n in numbers:
            res: TrackResult | None = by_number.get(n)
            meta = _track_meta(self.release, n)
            toc_track = toc.track(n) if n > 0 else None
            path = self.files.get(n)
            tracks.append(
                TrackReport(
                    number=n,
                    title=meta.title,
                    artist=meta.artist,
                    filename=path.name if path else "",
                    start=res.start if res else (toc_track.start if toc_track else 0),
                    sectors=res.sectors if res else 0,
                    pregap=self.pregaps.get(n),
                    pregap_peak=self.gap_peaks.get(n),
                    isrc=meta.isrcs[0] if meta.isrcs else None,
                    preemphasis=bool(toc_track and toc_track.preemphasis),
                    copy_permitted=bool(toc_track and toc_track.copy_permitted),
                    rip=res,
                    ar=ar.tracks.get(n),
                    error=self.errors.get(n),
                )
            )
        inq = self.drive
        return AlbumReport(
            app_version=__version__,
            created=self._created,
            drive=inq,
            profile=self.profile,
            settings=self.extraction,
            release=self.release,
            toc=toc,
            tracks=tuple(tracks),
            mb_disc_id=self.disc_id,
            cddb_id=cddb_disc_id(toc),
            ar_disc=ar.disc.filename,
            ar_in_database=self.ar_in_database,
            ar_error=self.ar_error,
            pressings=ar.pressings,
            mcn=self.disc.mcn,
            flac_level=self.settings.flac_level,
            replaygain=self.settings.replaygain,
            cancelled=rip.cancelled,
            notes=tuple(self.notes),
        )


def _write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
