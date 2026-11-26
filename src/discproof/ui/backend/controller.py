"""The Backend QObject that QML talks to.

All drive, network and encoding work runs on worker threads; results come back to
the GUI thread through :class:`Dispatcher`. QML only ever sees plain properties
(strings, numbers, maps, lists) and the :class:`TrackModel`.

States: ``nodrive`` -> ``empty`` (no disc) -> ``reading`` (TOC) -> ``lookup``
(metadata) -> ``ready`` -> ``ripping`` -> ``done``; ``error`` for failures.
"""

from __future__ import annotations

import copy
import datetime as dt
import functools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import Property, QBuffer, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from discproof.config.drives import DriveRecord, DriveStore, OffsetSource
from discproof.config.settings import load_settings, save_settings
from discproof.drive.device import DiscInfo, Drive
from discproof.drive.enumerate import DriveInfo, list_drives
from discproof.drive.msf import frames_to_timestamp
from discproof.drive.sense import DriveError, MediumNotPresentError, NotReadyError
from discproof.engine.events import (
    BlockDone,
    Event,
    PassStarted,
    Progress,
    Retrying,
    RipFinished,
    RipStarted,
    TrackDone,
)
from discproof.engine.extract import Extractor, track_spans
from discproof.engine.gaps import GapDetector, GapResult
from discproof.engine.profile import DriveProfile, ExtractionSettings, Mode, Quality
from discproof.engine.source import AudioSource
from discproof.metadata.coverart import Cover, CoverError, prepare_cover
from discproof.metadata.model import ReleaseMeta, Source, TrackMeta
from discproof.metadata.service import MetadataService, blank_release, release_from_cdtext
from discproof.metadata.web import ResponseCache, Web
from discproof.output.album import (
    AlbumOutput,
    AlbumWriter,
    ExistingPolicy,
    OutputSettings,
    folder_conflict,
    track_name,
)
from discproof.output.naming import TemplateError
from discproof.ui.backend.dispatch import Dispatcher, run_in_thread
from discproof.ui.backend.images import CoverProvider, SectorMaps
from discproof.ui.backend.models import TrackModel, TrackRow
from discproof.ui.backend.notify import notify
from discproof.verify.accuraterip import ArResponse, ar_disc_id
from discproof.verify.offsets import find_drive_offset
from discproof.verify.online import AccurateRipClient, OnlineError, drive_offset_list

log = logging.getLogger(__name__)

PROGRESS_INTERVAL_S = 0.2


class DriveSession(AudioSource, Protocol):
    """What the backend needs from an opened drive (a real Drive, or a fake in tests)."""

    def inquiry(self) -> Any: ...
    def wait_ready(self, timeout_s: float = 30.0, poll_s: float = 0.5) -> bool: ...
    def read_disc_info(self, *, isrc: bool = True, cdtext: bool = True) -> DiscInfo: ...
    def read_mcn(self) -> str | None: ...
    def read_cdtext(self) -> Any: ...
    def read_isrc(self, track: int) -> str | None: ...
    def eject(self) -> None: ...
    def close(self) -> None: ...


DriveFactory = Callable[[str], DriveSession]


def _open_drive(path: str) -> DriveSession:
    return Drive.open(path)


def _length(sectors: int) -> str:
    m, rest = divmod(sectors, 75 * 60)
    return f"{m}:{rest // 75:02d}"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Backend(QObject):
    stateChanged = Signal()
    messageChanged = Signal()
    drivesChanged = Signal()
    albumChanged = Signal()
    releasesChanged = Signal()
    coverChanged = Signal()
    ripChanged = Signal()
    resultsChanged = Signal()
    settingsChanged = Signal()
    driveProfileChanged = Signal()
    # One-off requests to the UI
    chooseReleaseRequested = Signal()
    folderConflict = Signal(str)
    setupDriveRequested = Signal()

    def __init__(
        self,
        *,
        drive_factory: DriveFactory = _open_drive,
        enumerate_drives: Callable[[], list[DriveInfo]] = list_drives,
        metadata: MetadataService | None = None,
        ar_client: AccurateRipClient | None = None,
        store: DriveStore | None = None,
        settings_file: Path | None = None,
        cover_provider: CoverProvider | None = None,
        sector_maps: SectorMaps | None = None,
        notifier: Callable[[str, str], object] = notify,
        offset_list: Callable[[], list[Any]] = drive_offset_list,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._dispatch = Dispatcher()
        self._factory = drive_factory
        self._enumerate = enumerate_drives
        self._settings_file = settings_file
        self._settings = load_settings(settings_file)
        self._metadata = metadata
        self._ar_client = ar_client
        self._store = store if store is not None else DriveStore()
        self._notify = notifier
        self._offset_list = offset_list
        self._clock = clock
        self.cover_provider = cover_provider or CoverProvider()
        self.sector_maps = sector_maps or SectorMaps()
        self.tracks = TrackModel()

        self._state = "nodrive"
        self._message = ""
        self._drives: list[DriveInfo] = []
        self._drive_index = -1
        self._busy = threading.Lock()
        self._disc: DiscInfo | None = None
        self._inquiry: Any = None
        self._record: DriveRecord | None = None
        self._releases: list[ReleaseMeta] = []
        self._release_index = -1
        self._release: ReleaseMeta | None = None  # the edited copy
        self._needs_choice = False
        self._cover: Cover | None = None
        self._cover_serial = 0
        self._ar: tuple[list[ArResponse] | None, bool | None, str | None] = (None, None, None)
        self._rip: dict[str, Any] = {"active": False}
        self._results: dict[str, Any] = {"visible": False}
        self._extractor: Extractor | None = None
        self._last_progress = 0.0
        self._map_versions: dict[int, int] = {}
        self._generation = 0  # bumps on every new disc; stale worker results are dropped
        # CD-Text and ISRCs are slow (~6 s each on some drives); read after the TOC.
        self._extras_thread: threading.Thread | None = None
        self._extras_cancel = threading.Event()
        self._edited = False  # the user changed album/track details

    # ------------------------------------------------------------ properties

    def _get_state(self) -> str:
        return self._state

    state = Property(str, _get_state, notify=stateChanged)

    def _get_message(self) -> str:
        return self._message

    message = Property(str, _get_message, notify=messageChanged)

    def _get_disc_present(self) -> bool:
        return self._disc is not None

    discPresent = Property(bool, _get_disc_present, notify=stateChanged)

    def _get_drives(self) -> list[dict[str, str]]:
        return [{"path": d.path, "name": f"{d.name}" or d.path} for d in self._drives]

    drives = Property("QVariantList", _get_drives, notify=drivesChanged)

    def _get_drive_index(self) -> int:
        return self._drive_index

    driveIndex = Property(int, _get_drive_index, notify=drivesChanged)

    def _get_track_model(self) -> TrackModel:
        return self.tracks

    trackModel = Property(QObject, _get_track_model, constant=True)

    def _get_album(self) -> dict[str, Any]:
        r = self._release
        if r is None:
            return {"present": False}
        src_label = {
            Source.MUSICBRAINZ: "MusicBrainz",
            Source.DISCOGS: "Discogs",
            Source.CDTEXT: "CD-Text",
            Source.MANUAL: "Manual",
        }[r.source]
        return {
            "present": True,
            "title": r.title,
            "artist": r.artist,
            "year": r.date or "",
            "genre": r.genre or "",
            "discNumber": r.disc_number,
            "discTotal": r.disc_total,
            "source": src_label,
            "exact": r.exact,
            "summary": r.summary(),
            "trackCount": len(r.tracks),
        }

    album = Property("QVariantMap", _get_album, notify=albumChanged)

    def _get_releases(self) -> list[dict[str, Any]]:
        return [
            {
                "title": f"{r.artist} — {r.title}",
                "summary": r.summary() or r.source.value,
                "source": r.source.value,
                "exact": r.exact,
            }
            for r in self._releases
        ]

    releases = Property("QVariantList", _get_releases, notify=releasesChanged)

    def _get_release_index(self) -> int:
        return self._release_index

    releaseIndex = Property(int, _get_release_index, notify=releasesChanged)

    def _get_needs_choice(self) -> bool:
        return self._needs_choice

    needsChoice = Property(bool, _get_needs_choice, notify=releasesChanged)

    def _get_cover_url(self) -> str:
        return f"image://cover/{self._cover_serial}" if self._cover else ""

    coverUrl = Property(str, _get_cover_url, notify=coverChanged)

    def _get_cover_info(self) -> str:
        c = self._cover
        return f"{c.width} × {c.height}" if c else ""

    coverInfo = Property(str, _get_cover_info, notify=coverChanged)

    def _get_rip(self) -> dict[str, Any]:
        return dict(self._rip)

    rip = Property("QVariantMap", _get_rip, notify=ripChanged)

    def _get_results(self) -> dict[str, Any]:
        return dict(self._results)

    results = Property("QVariantMap", _get_results, notify=resultsChanged)

    def _get_settings(self) -> dict[str, Any]:
        return asdict(self._settings)

    settings = Property("QVariantMap", _get_settings, notify=settingsChanged)

    def _get_drive_profile(self) -> dict[str, Any]:
        rec = self._record
        if rec is None:
            return {"known": False}
        return {
            "known": True,
            "configured": rec.features_tested is not None,
            "offset": rec.profile.read_offset,
            "offsetSource": rec.offset_source.value,
            "caches": rec.profile.caches_audio,
            "accurateStream": rec.profile.accurate_stream,
            "c2": rec.profile.use_c2,
            "name": f"{rec.vendor} {rec.model}",
        }

    driveProfile = Property("QVariantMap", _get_drive_profile, notify=driveProfileChanged)

    # ------------------------------------------------------------ helpers

    def _set_state(self, state: str, message: str | None = None) -> None:
        if state != self._state:
            self._state = state
            self.stateChanged.emit()
        if message is not None:
            self._set_message(message)

    def _set_message(self, message: str) -> None:
        if message != self._message:
            self._message = message
            self.messageChanged.emit()

    def _post(self, fn: Callable[[], Any]) -> None:
        self._dispatch.post(fn)

    def post(self, fn: Callable[[], Any]) -> None:
        """Run ``fn`` on the GUI thread (for helpers such as the drive wizard)."""
        self._dispatch.post(fn)

    def open_drive(self, path: str) -> DriveSession:
        return self._factory(path)

    def _service(self) -> MetadataService:
        if self._metadata is None:
            s = self._settings
            self._metadata = MetadataService(
                Web(cache=ResponseCache()),
                discogs_token=s.discogs_token or None,
                use_discogs=s.use_discogs,
            )
        return self._metadata

    def _ar_service(self) -> AccurateRipClient:
        if self._ar_client is None:
            self._ar_client = AccurateRipClient()
        return self._ar_client

    @property
    def drive_path(self) -> str | None:
        if 0 <= self._drive_index < len(self._drives):
            return self._drives[self._drive_index].path
        return None

    # ------------------------------------------------------------ drives & disc

    @Slot()
    def refreshDrives(self) -> None:
        try:
            self._drives = self._enumerate()
        except OSError as exc:
            self._drives = []
            log.warning("cannot list drives: %s", exc)
        last = self._settings.last_drive
        paths = [d.path for d in self._drives]
        self._drive_index = paths.index(last) if last in paths else (0 if paths else -1)
        self.drivesChanged.emit()
        if self._drive_index < 0:
            self._clear_disc()
            self._set_state("nodrive", "No optical drive found")
        else:
            self.loadDisc()

    @Slot(int)
    def selectDrive(self, index: int) -> None:
        if index == self._drive_index or not 0 <= index < len(self._drives):
            return
        self._drive_index = index
        self.drivesChanged.emit()
        self._settings = replace(self._settings, last_drive=self._drives[index].path)
        self._save_settings()
        self.loadDisc()

    def _clear_disc(self, *, keep_results: bool = False) -> None:
        """Forget the disc. With ``keep_results`` (after a rip) the album, tracks and
        results stay on screen until another disc is loaded."""
        self._generation += 1
        self._disc = None
        self.stateChanged.emit()  # discPresent
        if keep_results and self._results.get("visible"):
            return
        self._releases, self._release, self._release_index = [], None, -1
        self._needs_choice = False
        self._cover = None
        self.cover_provider.set_jpeg(None)
        self._ar = (None, None, None)
        self._results = {"visible": False}
        self.tracks.reset([])
        self.albumChanged.emit()
        self.releasesChanged.emit()
        self.coverChanged.emit()
        self.resultsChanged.emit()

    @Slot()
    def loadDisc(self) -> None:
        """(Re)read the disc in the current drive, then look it up."""
        path = self.drive_path
        if path is None or self._state == "ripping":
            return
        keep = self._results.get("visible", False)
        self._clear_disc(keep_results=keep)
        gen = self._generation
        if not keep:
            self._set_state("reading", "Reading disc…")

        self._stop_extras()
        cancel = threading.Event()
        self._extras_cancel = cancel

        def work() -> None:
            try:
                drive = self._factory(path)
            except DriveError as exc:
                msg = str(exc)
                self._post(lambda: self._set_state("error", msg))
                return
            try:
                inquiry = drive.inquiry()
                if not drive.wait_ready(3.0):
                    self._post(lambda: self._disc_missing(gen, inquiry))
                    return
                # Fast part: TOC and MCN (milliseconds) -> show the disc, start lookup.
                toc = drive.read_toc()
                info = DiscInfo(toc=toc, mcn=drive.read_mcn())
                self._post(lambda: self._disc_loaded(gen, inquiry, info))
                self._read_extras(drive, gen, info, cancel)
            except (MediumNotPresentError, NotReadyError):
                self._post(lambda: self._disc_missing(gen, None))
            except DriveError as exc:
                msg = f"Cannot read the disc: {exc}"
                self._post(lambda: self._set_state("error", msg))
            finally:
                drive.close()

        self._extras_thread = run_in_thread("read-disc", work)

    #: If this many leading tracks have no ISRC, assume none do (each miss can take
    #: ~6 s while the drive scans the sub-channel; discs carry ISRCs on all tracks or
    #: none, and MusicBrainz supplies them too).
    ISRC_PROBE_TRACKS = 2

    def _read_extras(
        self, drive: DriveSession, gen: int, info: DiscInfo, cancel: threading.Event
    ) -> None:
        """Slow part, still on the reading thread: CD-Text, then ISRCs."""
        if cancel.is_set():
            return
        try:
            cdtext = drive.read_cdtext()
        except DriveError:
            cdtext = None
        if cdtext is not None and not cancel.is_set():
            self._post(lambda: self._cdtext_read(gen, cdtext))
        isrcs: dict[int, str] = {}
        for i, track in enumerate(info.toc.audio_tracks):
            if cancel.is_set():
                return
            if i >= self.ISRC_PROBE_TRACKS and not isrcs:
                break
            try:
                code = drive.read_isrc(track.number)
            except DriveError:
                code = None
            if code:
                isrcs[track.number] = code
        if isrcs and not cancel.is_set():
            self._post(lambda: self._isrcs_read(gen, isrcs))

    def _stop_extras(self, wait: float = 0.0) -> None:
        self._extras_cancel.set()
        t = self._extras_thread
        if wait and t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(wait)

    def _cdtext_read(self, gen: int, cdtext: Any) -> None:
        if gen != self._generation or self._disc is None:
            return
        self._disc.cdtext = cdtext
        release = release_from_cdtext(cdtext, self._disc.toc)
        if release is None:
            return
        online = [r for r in self._releases if r.source in (Source.MUSICBRAINZ, Source.DISCOGS)]
        if online:
            self._releases.append(release)  # offered in the chooser
            self.releasesChanged.emit()
        elif not self._edited:
            self._set_release([release], 0, needs_choice=False)
            self._set_message("Not found online — using CD-Text")

    def _isrcs_read(self, gen: int, isrcs: dict[int, str]) -> None:
        if gen != self._generation or self._disc is None:
            return
        self._disc.isrcs.update(isrcs)
        for r in [*self._releases, *([self._release] if self._release else [])]:
            for t in r.tracks:
                code = isrcs.get(t.number)
                if code:
                    t.isrcs = (code, *(i for i in t.isrcs if i != code))

    def _disc_missing(self, gen: int, inquiry: Any) -> None:
        if gen != self._generation:
            return
        if inquiry is not None:
            self._use_drive(inquiry)
        if self._results.get("visible"):
            self._set_state("done", "Insert the next CD")
        else:
            self._set_state("empty", "Insert an audio CD")

    def _use_drive(self, inquiry: Any) -> None:
        if self._inquiry is not None and (
            (self._inquiry.vendor, self._inquiry.product, self._inquiry.revision)
            == (inquiry.vendor, inquiry.product, inquiry.revision)
        ):
            return
        self._inquiry = inquiry
        self._record = self._store.get_or_create(inquiry.vendor, inquiry.product, inquiry.revision)
        self.driveProfileChanged.emit()
        if self._record.offset_source is OffsetSource.DEFAULT:
            self._apply_list_offset(self._record)

    def _apply_list_offset(self, record: DriveRecord) -> None:
        """A drive nobody has set up yet: use the AccurateRip drive list's offset until
        the wizard confirms it (the list is downloaded at most monthly)."""

        def work() -> None:
            offset, _ = self.drive_list_offset()
            if offset is not None:
                self._post(lambda: self._list_offset_found(record, offset))

        run_in_thread("drive-list", work)

    def _list_offset_found(self, record: DriveRecord, offset: int) -> None:
        if record is not self._record or record.offset_source is not OffsetSource.DEFAULT:
            return
        record.set_offset(offset, OffsetSource.DRIVE_LIST)
        try:
            self._store.save()
        except OSError as exc:
            log.info("cannot save drive profile: %s", exc)
        self.driveProfileChanged.emit()

    def _disc_loaded(self, gen: int, inquiry: Any, info: DiscInfo) -> None:
        if gen != self._generation:
            return
        if self._results.get("visible"):  # a new disc replaces the last rip's results
            self._results = {"visible": False}
            self.resultsChanged.emit()
            self._cover = None
            self.cover_provider.set_jpeg(None)
            self.coverChanged.emit()
        self._use_drive(inquiry)
        self._disc = info
        self.stateChanged.emit()
        toc = info.toc
        if not toc.audio_tracks:
            self._set_state("error", "This disc has no audio tracks")
            return
        self._edited = False
        self._set_release([blank_release(toc)], 0, needs_choice=False)
        self._set_state("ready", f"{len(toc.audio_tracks)} audio tracks")
        if self._record is not None and self._record.features_tested is None:
            self.setupDriveRequested.emit()
        if self._settings.auto_lookup:
            self.lookup()

    def _rows_for(self, release: ReleaseMeta) -> list[TrackRow]:
        assert self._disc is not None
        toc = self._disc.toc
        by_number = {t.number: t for t in release.tracks}
        rows = []
        for t in toc.tracks:
            meta = by_number.get(t.number)
            rows.append(
                TrackRow(
                    number=t.number,
                    title=meta.title
                    if meta
                    else ("Data track" if t.is_data else f"Track {t.number:02d}"),
                    artist=meta.artist if meta else "",
                    length=_length(toc.length(t.number)),
                    selected=t.is_audio,
                    status="pending" if t.is_audio else "skipped",
                    data_track=t.is_data,
                )
            )
        return rows

    def _set_release(
        self, releases: list[ReleaseMeta], index: int | None, *, needs_choice: bool
    ) -> None:
        self._releases = releases
        self._release_index = index if index is not None else 0
        self._release = copy.deepcopy(releases[self._release_index]) if releases else None
        self._needs_choice = needs_choice
        if self._release is not None:
            selected = {r.number: r.selected for r in self.tracks.rows}
            rows = self._rows_for(self._release)
            for r in rows:
                if r.number in selected:
                    r.selected = selected[r.number]
            self.tracks.reset(rows)
        self.releasesChanged.emit()
        self.albumChanged.emit()

    # ------------------------------------------------------------ metadata

    @Slot()
    def lookup(self) -> None:
        disc = self._disc
        if disc is None:
            return
        gen = self._generation
        self._set_state("lookup", "Looking up the album…")
        use_mb = self._settings.use_musicbrainz

        def work() -> None:
            service = self._service()
            if use_mb:
                result = service.lookup(disc)
                releases, pre, errors = result.releases, result.preselected, result.errors
            else:
                releases, pre, errors = [blank_release(disc.toc)], 0, []
            self._post(lambda: self._lookup_done(gen, releases, pre, errors))
            # AccurateRip answer, needed when ripping: fetch it now.
            try:
                ar = self._ar_service().lookup(ar_disc_id(disc.toc))
                state: tuple[list[ArResponse] | None, bool | None, str | None] = (
                    ar,
                    ar is not None,
                    None,
                )
            except OnlineError as exc:
                state = (None, None, str(exc))
            self._post(lambda: self._ar_done(gen, state))

        run_in_thread("lookup", work)

    def _lookup_done(
        self, gen: int, releases: list[ReleaseMeta], pre: int | None, errors: list[str]
    ) -> None:
        if gen != self._generation:
            return
        needs = pre is None and len(releases) > 1
        self._set_release(releases, pre, needs_choice=needs)
        found = self._release
        msg = "; ".join(errors) if errors else ""
        if found is not None and found.source in (Source.MUSICBRAINZ, Source.DISCOGS):
            msg = msg or ("Exact match" if found.exact else "Possible match — please check")
        elif found is not None and found.source is Source.CDTEXT:
            msg = msg or "Not found online — using CD-Text"
        else:
            msg = msg or "Not found online — enter the details by hand"
        self._set_state("ready", msg)
        if needs:
            self.chooseReleaseRequested.emit()
        self._fetch_cover()
        if self._settings.auto_rip and not needs and found and found.source is not Source.MANUAL:
            self.startRip("")

    def _ar_done(
        self, gen: int, state: tuple[list[ArResponse] | None, bool | None, str | None]
    ) -> None:
        if gen == self._generation:
            self._ar = state

    def _fetch_cover(self) -> None:
        release = self._release
        if release is None or release.source is Source.MANUAL:
            return
        gen = self._generation

        def work() -> None:
            cover = self._service().cover(release)
            self._post(lambda: self._cover_done(gen, cover))

        run_in_thread("cover", work)

    def _cover_done(self, gen: int, cover: Cover | None) -> None:
        if gen == self._generation and cover is not None and self._cover is None:
            self._set_cover(cover)

    def _set_cover(self, cover: Cover | None) -> None:
        self._cover = cover
        self.cover_provider.set_jpeg(cover.cover_jpeg if cover else None)
        self._cover_serial += 1
        self.coverChanged.emit()

    @Slot(int)
    def chooseRelease(self, index: int) -> None:
        if not 0 <= index < len(self._releases):
            return
        self._set_release(self._releases, index, needs_choice=False)
        self._cover = None
        self.cover_provider.set_jpeg(None)
        self.coverChanged.emit()
        self._fetch_cover()

    # ------------------------------------------------------------ editing

    @Slot(str, str)
    def setAlbumField(self, field: str, value: str) -> None:
        r = self._release
        if r is None:
            return
        self._edited = True
        value = value.strip()
        if field == "title":
            r.title = value
        elif field == "artist":
            for t in r.tracks:
                if not t.artist or t.artist == r.artist:
                    t.artist = value
            r.artist = value
            self.tracks.reset(self._rows_keep_state(r))
        elif field == "year":
            r.date = value or None
        elif field == "genre":
            r.genre = value or None
        elif field in ("discNumber", "discTotal"):
            try:
                n = max(1, int(value))
            except ValueError:
                return
            if field == "discNumber":
                r.disc_number = n
            else:
                r.disc_total = n
        else:
            return
        self.albumChanged.emit()

    def _rows_keep_state(self, r: ReleaseMeta) -> list[TrackRow]:
        old = {row.number: row for row in self.tracks.rows}
        rows = self._rows_for(r)
        for row in rows:
            o = old.get(row.number)
            if o is not None:
                row.selected, row.status, row.crc = o.selected, o.status, o.crc
                row.confidence, row.map_version = o.confidence, o.map_version
        return rows

    @Slot(int, str, str)
    def setTrackField(self, number: int, field: str, value: str) -> None:
        r = self._release
        if r is None:
            return
        self._edited = True
        for t in r.tracks:
            if t.number == number:
                if field == "title":
                    t.title = value.strip()
                    self.tracks.update(number, title=t.title)
                elif field == "artist":
                    t.artist = value.strip()
                    self.tracks.update(number, artist=t.artist)
                return

    @Slot(int, bool)
    def setTrackSelected(self, number: int, selected: bool) -> None:
        self.tracks.update(number, selected=selected)

    @Slot(bool)
    def selectAll(self, selected: bool) -> None:
        for row in self.tracks.rows:
            if not row.data_track:
                self.tracks.update(row.number, selected=selected)

    @Slot(QUrl)
    def setCoverFromUrl(self, url: QUrl) -> None:
        path = url.toLocalFile()
        if not path:
            return
        try:
            self._set_cover(prepare_cover(Path(path).read_bytes(), url.toString()))
            self._set_message("Cover replaced")
        except (OSError, CoverError) as exc:
            self._set_message(f"Cannot use that image: {exc}")

    @Slot()
    def pasteCover(self) -> None:
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            urls = QGuiApplication.clipboard().mimeData().urls()
            if urls:
                self.setCoverFromUrl(urls[0])
            else:
                self._set_message("The clipboard holds no image")
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        try:
            self._set_cover(prepare_cover(bytes(buf.data().data())))
            self._set_message("Cover pasted")
        except CoverError as exc:
            self._set_message(f"Cannot use that image: {exc}")

    @Slot()
    def removeCover(self) -> None:
        self._set_cover(None)

    # ------------------------------------------------------------ settings

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        current = self._settings
        if not hasattr(current, key):
            return
        old = getattr(current, key)
        try:
            if isinstance(old, bool):
                value = bool(value)
            elif isinstance(old, int):
                value = int(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            return
        self._settings = replace(current, **{key: value}).validated()
        if key in ("discogs_token", "use_discogs"):
            self._metadata = None  # rebuilt with the new token on next use
        self._save_settings()
        self.settingsChanged.emit()

    def _save_settings(self) -> None:
        try:
            save_settings(self._settings, self._settings_file)
        except OSError as exc:
            self._set_message(f"Cannot save settings: {exc}")

    @Slot(str, result=str)
    def templatePreview(self, template: str) -> str:
        """Where track 1 would go. Shows disc 2 of a 2-disc set when the loaded album
        is single-disc, so the multi-disc layout is visible too."""
        r = self._release
        multi = r is not None and r.disc_total > 1
        disc_number, disc_total = (r.disc_number, r.disc_total) if multi and r else (2, 2)
        sample = ReleaseMeta(
            Source.MANUAL,
            r.title if r else "Anodyne",
            r.artist if r else "Uncle Tupelo",
            [
                TrackMeta(
                    1,
                    r.tracks[0].title if r and r.tracks else "Slate",
                    r.tracks[0].artist if r and r.tracks else "Uncle Tupelo",
                )
            ],
            date=r.date if r else "1993",
            disc_number=disc_number,
            disc_total=disc_total,
        )
        settings = replace(
            self._output_settings(ExistingPolicy.SUFFIX), template=template, library=Path()
        )
        try:
            folder, filename = track_name(settings, sample, 1)
        except TemplateError as exc:
            return f"⚠ {exc}"
        return "/".join((*folder.parts, filename))

    # ------------------------------------------------------------ ripping

    def _output_settings(self, policy: ExistingPolicy) -> OutputSettings:
        s = self._settings
        return OutputSettings(
            Path(s.library).expanduser(),
            s.template,
            s.flac_level,
            s.replaygain,
            policy,
            disc_subfolders=s.disc_subfolders,
        )

    def _extraction_settings(self, mode: str | None = None) -> ExtractionSettings:
        s = self._settings
        mode = mode or s.mode
        return ExtractionSettings(
            mode=Mode.BURST if mode == "burst" else Mode.SECURE,
            test_and_copy=(mode == "paranoid") or (s.test_and_copy and mode != "burst"),
            quality={"low": Quality.LOW, "medium": Quality.MEDIUM}.get(s.quality, Quality.HIGH),
            tracks=tuple(self.tracks.selected_numbers()),
            rip_htoa=s.htoa == "track00",
        )

    @Slot(str)
    def startRip(self, mode: str = "") -> None:
        if self._disc is None or self._release is None or self._state == "ripping":
            return
        if not self.tracks.selected_numbers():
            self._set_message("Select at least one track")
            return
        self._pending_mode = mode or self._settings.mode
        policy_name = self._settings.existing
        if policy_name == "ask":
            try:
                conflict = folder_conflict(
                    self._output_settings(ExistingPolicy.SUFFIX), self._release
                )
            except TemplateError as exc:
                self._set_message(f"Naming template: {exc}")
                return
            if conflict is not None:
                self.folderConflict.emit(str(conflict))
                return
            policy_name = "suffix"
        self._start_rip(ExistingPolicy(policy_name), self._pending_mode)

    @Slot(str)
    def resolveConflict(self, policy: str) -> None:
        if policy in ("overwrite", "suffix", "skip"):
            if policy == "skip":
                self._set_message("Rip skipped: the album folder already exists")
                return
            self._start_rip(
                ExistingPolicy(policy), getattr(self, "_pending_mode", self._settings.mode)
            )

    def _profile(self) -> DriveProfile:
        return self._record.profile if self._record else DriveProfile()

    def _start_rip(self, policy: ExistingPolicy, mode: str) -> None:
        disc, release, path = self._disc, copy.deepcopy(self._release), self.drive_path
        if disc is None or release is None or path is None:
            return
        profile = self._profile()
        settings = self._extraction_settings(mode)
        out_settings = self._output_settings(policy)
        ar_responses, ar_in_db, ar_error = self._ar
        cover = self._cover
        inquiry = self._inquiry
        drive_name = (
            f"{inquiry.vendor} {inquiry.product} (firmware {inquiry.revision})" if inquiry else path
        )
        spans = [(s.number, s.start, s.end) for s in track_spans(disc.toc, settings)]
        self.sector_maps.reset(spans)
        self._map_versions = {}
        selected = set(settings.tracks or ())
        for row in self.tracks.rows:
            if row.number in selected:
                self.tracks.update(row.number, status="pending", crc="", confidence=0, progress=0.0)
        self._rip = {
            "active": True,
            "paused": False,
            "pass": "",
            "overall": 0.0,
            "track": 0,
            "speed": "",
            "elapsed": "0:00",
            "eta": "",
            "mode": mode,
            "tracks": len(spans),
        }
        self._results = {"visible": False}
        self.resultsChanged.emit()
        self.ripChanged.emit()
        self._set_state("ripping", "Ripping…")
        self._started = self._clock()
        self._passes = 2 if settings.test_and_copy else 1
        self._pass_index = 0
        gen = self._generation
        eject = self._settings.eject_when_done
        cancel = threading.Event()
        self._rip_cancel = cancel

        def work() -> None:
            output: AlbumOutput | None = None
            error: str | None = None
            self._stop_extras(wait=15.0)  # the rip needs the drive to itself
            try:
                drive = self._factory(path)
            except DriveError as exc:
                msg = str(exc)
                self._post(lambda: self._rip_failed(gen, msg))
                return
            try:
                pregaps, peaks, notes = self._detect_gaps(drive, disc, settings, gen, cancel)
                if cancel.is_set():
                    self._post(lambda: self._rip_failed(gen, "Rip cancelled"))
                    return
                writer = AlbumWriter(
                    out_settings,
                    release,
                    disc,
                    drive=drive_name,
                    profile=profile,
                    extraction=settings,
                    cover=cover,
                    ar_responses=ar_responses,
                    ar_in_database=ar_in_db,
                    ar_error=ar_error,
                    pregaps=pregaps,
                    gap_peaks=peaks,
                    notes=notes,
                )
                if writer.prepare() is None:
                    self._post(lambda: self._rip_failed(gen, "The album folder already exists"))
                    return
                extractor = Extractor(
                    drive,
                    profile,
                    settings,
                    toc=disc.toc,
                    on_event=lambda e: self._on_event(gen, e, writer),
                    on_track=writer.on_track,
                    clock=self._clock,
                )
                self._extractor = extractor
                result = extractor.run()
                output = writer.finish(result)
                if eject and not result.cancelled:
                    try:
                        drive.eject()
                    except DriveError as exc:
                        log.info("eject failed: %s", exc)
            except DriveError as exc:
                error = f"Drive error: {exc}"
            except Exception as exc:  # report anything rather than dying silently
                log.exception("rip failed")
                error = f"Rip failed: {exc}"
            finally:
                self._extractor = None
                drive.close()
            if error is not None:
                err = error
                self._post(lambda: self._rip_failed(gen, err))
            elif output is not None:
                out = output
                self._post(lambda: self._rip_done(gen, out))

        run_in_thread("rip", work)

    # Called on the rip thread.
    def _detect_gaps(
        self,
        drive: DriveSession,
        disc: DiscInfo,
        settings: ExtractionSettings,
        gen: int,
        cancel: threading.Event,
    ) -> tuple[dict[int, int], dict[int, int | None], list[str]]:
        """Pre-gaps of the ripped tracks and of the tracks after them (their INDEX 00
        lies in the ripped file), plus track 1's hidden-track check."""
        if not self._settings.detect_gaps:
            return {}, {}, []
        toc = disc.toc
        audio = [t.number for t in toc.audio_tracks]
        selected = set(settings.tracks or audio)
        wanted = sorted((selected | {n + 1 for n in selected} | {audio[0]}) & set(audio))
        self._post(lambda: self._gaps_progress(gen, 0, len(wanted)))
        detector = GapDetector(drive, toc)
        results: dict[int, GapResult] = {}
        for i, n in enumerate(wanted):
            if cancel.is_set():
                break
            results[n] = detector.detect(n)
            self._post(functools.partial(self._gaps_progress, gen, i + 1, len(wanted)))
        pregaps = {n: r.pregap for n, r in results.items()}
        peaks = {n: r.peak for n, r in results.items()}
        notes: list[str] = []
        first = toc.track(audio[0])
        htoa = results.get(first.number)
        if (
            first.number == 1
            and first.start > 0
            and htoa is not None
            and htoa.quiet is False
            and not settings.rip_htoa
        ):
            notes.append(
                f"The hidden track before track 1 ({frames_to_timestamp(first.start)}) "
                'contains audio and was not ripped; choose "Rip as track 00" in Settings '
                "to keep it"
            )
        for n, r in sorted(results.items()):
            if r.pregap and not r.confirmed and n != first.number:
                notes.append(f"The pre-gap of track {n} could not be confirmed (±2 sectors)")
        self._post(lambda: self._gaps_done(gen, results))
        return pregaps, peaks, notes

    def _gaps_progress(self, gen: int, done: int, total: int) -> None:
        if gen != self._generation:
            return
        self._rip["pass"] = "gaps"
        self._rip["gaps"] = f"{done}/{total}"
        self.ripChanged.emit()

    def _gaps_done(self, gen: int, results: dict[int, GapResult]) -> None:
        if gen != self._generation:
            return
        for n, r in results.items():
            gap = "—" if r.pregap == 0 else frames_to_timestamp(r.pregap)
            self.tracks.update(n, pregap=gap)
        self._rip["pass"] = ""
        self.ripChanged.emit()

    def _on_event(self, gen: int, event: Event, writer: AlbumWriter) -> None:
        if isinstance(event, BlockDone):
            touched = self.sector_maps.mark(event.lba, list(event.statuses))
            if touched:
                self._post(lambda: self._maps_changed(gen, touched))
        elif isinstance(event, Progress):
            now = self._clock()
            if now - self._last_progress >= PROGRESS_INTERVAL_S or event.done == event.total:
                self._last_progress = now
                self._post(lambda: self._progress(gen, event))
        elif isinstance(event, TrackDone):
            verdict = writer.verdicts.get(event.result.number)
            self._post(lambda: self._track_done(gen, event, verdict))
        elif isinstance(event, (Retrying, PassStarted, RipStarted, RipFinished)):
            self._post(lambda: self._misc_event(gen, event))

    def _maps_changed(self, gen: int, touched: set[int]) -> None:
        if gen != self._generation:
            return
        for n in touched:
            v = self._map_versions.get(n, 0) + 1
            self._map_versions[n] = v
            self.tracks.update(n, map_version=v)

    def _track_at(self, lba: int) -> int:
        if self._disc is None:
            return 0
        current = 0
        for t in self._disc.toc.audio_tracks:
            if t.start <= lba:
                current = t.number
        return current

    def _progress(self, gen: int, e: Progress) -> None:
        if gen != self._generation:
            return
        frac_pass = e.done / max(e.total, 1)
        overall = (
            (self._pass_index - 1 + frac_pass) / self._passes if self._pass_index else frac_pass
        )
        elapsed = self._clock() - self._started
        eta = elapsed * (1 - overall) / overall if overall > 0.01 else 0
        track = self._track_at(e.lba - 1)
        prev = self._rip.get("track")
        if prev and prev != track:
            i = self.tracks.row_of(prev)
            if i >= 0 and self.tracks.rows[i].status in ("reading", "testing", "rereading"):
                self.tracks.update(prev, status="encoding")  # until TrackDone settles it
        if track:
            i = self.tracks.row_of(track)
            if i >= 0 and self.tracks.rows[i].status in ("pending", "reading", "testing"):
                self.tracks.update(track, status="testing" if e.pass_name == "test" else "reading")
        self._rip.update(
            overall=overall,
            track=track,
            speed=f"{e.speed_x:.1f}×",
            elapsed=_clock(elapsed),
            eta=_clock(eta) if overall > 0.01 else "",
        )
        self.ripChanged.emit()

    def _misc_event(self, gen: int, e: Event) -> None:
        if gen != self._generation:
            return
        if isinstance(e, PassStarted):
            self._pass_index += 1
            self._rip["pass"] = e.name
            if e.name == "copy" and self._passes == 2:
                for row in self.tracks.rows:
                    if row.selected and not row.data_track:
                        self.sector_maps.clear_track(row.number)
                        self.tracks.update(
                            row.number, status="pending", map_version=row.map_version + 1
                        )
            self.ripChanged.emit()
        elif isinstance(e, Retrying):
            n = self._track_at(e.lba)
            if n:
                self.tracks.update(n, status="rereading")

    def _track_done(self, gen: int, e: TrackDone, verdict: Any) -> None:
        if gen != self._generation:
            return
        r = e.result
        if e.pass_name == "test":
            self.tracks.update(r.number, status="pending", crc=f"{r.crc32:08X}")
            return
        if r.suspicious or r.crc_mismatch:
            status = "suspicious"
        elif verdict is not None and verdict.accurate:
            status = "accurate"
        elif verdict is not None and self._ar[1] and verdict.max_confidence:
            status = "notaccurate"
        else:
            status = "ok"
        self.tracks.update(
            r.number,
            status=status,
            crc=f"{r.crc32:08X}",
            confidence=verdict.confidence if verdict is not None else 0,
            progress=1.0,
        )

    def _rip_done(self, gen: int, out: AlbumOutput) -> None:
        report = out.report
        for t in report.tracks:
            if t.error:
                self.tracks.update(t.number, status="error")
        numbered = [t for t in report.tracks if t.number > 0]
        accurate = sum(1 for t in numbered if t.ar and t.ar.accurate)
        suspicious = sum(1 for t in report.tracks if t.rip and t.rip.suspicious)
        elapsed = self._clock() - self._started
        if report.cancelled:
            headline, tone = "Rip cancelled", "warning"
        elif out.errors or not report.ok:
            headline, tone = "Finished with errors", "negative"
        elif report.ar_in_database and accurate == len(numbered):
            headline, tone = f"All {len(numbered)} tracks accurately ripped", "positive"
        elif report.ar_in_database:
            headline, tone = f"{accurate} of {len(numbered)} tracks accurately ripped", "warning"
        elif report.ar_in_database is False:
            headline, tone = "Ripped — disc not in AccurateRip", "info"
        else:
            headline, tone = "Ripped — AccurateRip not checked", "info"
        max_conf = max((t.ar.confidence for t in numbered if t.ar and t.ar.accurate), default=0)
        self._results = {
            "visible": True,
            "headline": headline,
            "tone": tone,
            "accurate": accurate,
            "total": len(numbered),
            "suspicious": suspicious,
            "confidence": max_conf,
            "elapsed": _clock(elapsed),
            "folder": str(out.folder),
            "log": str(out.log),
            "errors": out.errors,
            "cancelled": report.cancelled,
        }
        self._rip = {"active": False}
        self.ripChanged.emit()
        self.resultsChanged.emit()
        self._set_state("done", headline)
        if self._settings.notify and gen == self._generation:
            album = f"{report.release.artist} — {report.release.title}"
            self._notify(headline, album)

    def _rip_failed(self, gen: int, message: str) -> None:
        self._rip = {"active": False}
        self.ripChanged.emit()
        self._set_state("error" if self._disc is None else "ready", message)

    @Slot()
    def pauseRip(self) -> None:
        if self._extractor is not None:
            self._extractor.pause()
            self._rip["paused"] = True
            self.ripChanged.emit()

    @Slot()
    def resumeRip(self) -> None:
        if self._extractor is not None:
            self._extractor.resume()
            self._rip["paused"] = False
            self.ripChanged.emit()

    @Slot()
    def cancelRip(self) -> None:
        cancel = getattr(self, "_rip_cancel", None)
        if cancel is not None:
            cancel.set()  # also stops gap detection, which runs before extraction
        if self._extractor is not None:
            self._extractor.cancel()
        self._set_message("Cancelling…")

    # ------------------------------------------------------------ results & misc

    @Slot()
    def openFolder(self) -> None:
        folder = self._results.get("folder")
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    @Slot(result=str)
    def logText(self) -> str:
        path = self._results.get("log")
        try:
            return Path(path).read_text(encoding="utf-8") if path else ""
        except OSError as exc:
            return f"Cannot read the log: {exc}"

    @Slot()
    def eject(self) -> None:
        path = self.drive_path
        if path is None or self._state == "ripping":
            return

        def work() -> None:
            try:
                drive = self._factory(path)
                try:
                    drive.eject()
                finally:
                    drive.close()
            except DriveError as exc:
                msg = f"Cannot eject: {exc}"
                self._post(lambda: self._set_message(msg))
                return
            self._post(self._ejected)

        run_in_thread("eject", work)

    def _ejected(self) -> None:
        keep = bool(self._results.get("visible"))
        self._clear_disc(keep_results=keep)
        self._set_state(
            "done" if keep else "empty", "Insert the next CD" if keep else "Insert an audio CD"
        )

    @Slot(str, str)
    def udevEvent(self, action: str, device: str) -> None:
        """A drive was plugged in or removed ("add"/"remove"), or the medium in a drive
        changed ("change"). Ignored while ripping (the rip owns the drive)."""
        if self._state == "ripping":
            return
        if action in ("add", "remove"):
            self.refreshDrives()
        elif action == "change" and device == self.drive_path:
            self.loadDisc()

    def apply_drive_record(self, record: DriveRecord) -> None:
        """The wizard saved a new profile for the current drive."""
        self._record = record
        self.driveProfileChanged.emit()

    def drive_list_offset(self) -> tuple[int | None, str]:
        """(offset, description) from the AccurateRip drive list for the current drive."""
        inq = self._inquiry
        if inq is None:
            return None, ""
        try:
            entry = find_drive_offset(self._offset_list(), inq.vendor, inq.product)
        except OnlineError as exc:
            return None, str(exc)
        if entry is None or entry.offset is None:
            return None, "not in the AccurateRip drive list"
        return entry.offset, f"{entry.vendor} - {entry.model} ({entry.submissions} submissions)"

    @property
    def store(self) -> DriveStore:
        return self._store

    @property
    def disc(self) -> DiscInfo | None:
        return self._disc

    @property
    def inquiry(self) -> Any:
        return self._inquiry

    @property
    def record(self) -> DriveRecord | None:
        return self._record

    @property
    def ar_state(self) -> tuple[list[ArResponse] | None, bool | None, str | None]:
        return self._ar

    def now(self) -> dt.datetime:
        return dt.datetime.now()


__all__ = ["Backend", "DriveSession"]
