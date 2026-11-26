"""Drive setup wizard: feature detection and read offset, saved to drives.toml."""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from discproof.config.drives import OffsetSource
from discproof.drive.sense import DriveError
from discproof.engine.profile import DriveProfile
from discproof.ui.backend.controller import Backend
from discproof.ui.backend.dispatch import run_in_thread
from discproof.verify.accuraterip import ar_disc_id
from discproof.verify.features import FeatureDetector, FeatureReport
from discproof.verify.offsets import offset_candidates
from discproof.verify.verify import detect_offset_from_disc


class DriveWizard(QObject):
    changed = Signal()

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self.backend = backend
        self._running = False
        self._step = ""
        self._steps: list[str] = []
        self._result: dict[str, Any] = {}
        self._report: FeatureReport | None = None
        self._candidates: list[int] = []
        self._list_offset: int | None = None
        self._offset: int | None = None
        self._error = ""

    def _get(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "step": self._step,
            "steps": list(self._steps),
            "result": dict(self._result),
            "error": self._error,
            "offset": self._offset if self._offset is not None else 0,
            "hasResult": self._report is not None,
        }

    status = Property("QVariantMap", _get, notify=changed)

    def _set_step(self, text: str) -> None:
        self._step = text
        self._steps.append(text)
        self.changed.emit()

    @Slot()
    def start(self) -> None:
        b = self.backend
        path, disc = b.drive_path, b.disc
        if self._running or path is None or disc is None:
            self._error = "Insert an audio CD first." if disc is None else ""
            self.changed.emit()
            return
        self._running, self._steps, self._error, self._report = True, [], "", None
        self._result = {}
        self.changed.emit()
        post = b.post
        responses, in_db, _ = b.ar_state
        base = b.record.profile if b.record else DriveProfile()

        def work() -> None:
            try:
                drive = b.open_drive(path)
            except DriveError as exc:
                msg = str(exc)
                post(lambda: self._failed(msg))
                return
            try:
                caps = None
                with contextlib.suppress(AttributeError, DriveError):
                    caps = drive.capabilities()  # type: ignore[attr-defined]
                max_x = caps.max_read_speed_x if caps and caps.max_read_speed_x else 48.0
                detector = FeatureDetector(
                    drive,
                    disc.toc,
                    max_speed_x=max_x,
                    on_step=lambda s: post(lambda: self._set_step(s)),
                )
                report = detector.run()
                post(lambda: self._set_step("Looking up the AccurateRip drive offset list"))
                list_offset, list_info = b.drive_list_offset()
                candidates: list[int] = []
                if responses:
                    post(lambda: self._set_step("Detecting the read offset from this disc"))
                    matches = detect_offset_from_disc(
                        drive, disc.toc, responses, report.apply(base)
                    )
                    candidates = offset_candidates(matches)
            except DriveError as exc:
                msg = str(exc)
                post(lambda: self._failed(msg))
                return
            finally:
                drive.close()
            post(lambda: self._finished(report, list_offset, list_info, candidates, in_db))

        run_in_thread("drive-wizard", work)

    def _failed(self, message: str) -> None:
        self._running = False
        self._error = message
        self.changed.emit()

    def _finished(
        self,
        report: FeatureReport,
        list_offset: int | None,
        list_info: str,
        candidates: list[int],
        in_db: bool | None,
    ) -> None:
        self._running = False
        self._report = report
        self._candidates = candidates
        self._list_offset = list_offset
        rec = self.backend.record
        confirmed = rec.confirmed_offset() if rec else None
        # Proposal: an offset confirmed by discs, else the drive list (if this disc
        # does not contradict it), else this disc's single candidate.
        if confirmed is not None:
            proposed = confirmed
        elif list_offset is not None and (not candidates or list_offset in candidates):
            proposed = list_offset
        elif len(candidates) == 1:
            proposed = candidates[0]
        else:
            proposed = rec.profile.read_offset if rec else 0
        self._offset = proposed
        c = report.cache
        if not candidates:
            disc_note = (
                "This disc is not in AccurateRip"
                if in_db is False
                else "AccurateRip could not be checked"
                if in_db is None
                else "No offset matched on this disc"
            )
        elif len(candidates) == 1:
            disc_note = f"This disc supports {candidates[0]:+d}"
        else:
            disc_note = f"This disc has pressings at {len(candidates)} offsets (ambiguous)"
        self._result = {
            "caches": c.caches_audio,
            "cacheSectors": c.cache_sectors or 0,
            "fuaWorks": bool(c.fua_works),
            "seekAway": bool(c.seek_away_works),
            "accurateStream": report.accurate_stream,
            "c2Supported": report.c2.supported,
            "c2Trusted": report.c2.trusted,
            "c2Tested": report.c2.tested,
            "overread": report.overread_leadin and report.overread_leadout,
            "listOffset": list_offset if list_offset is not None else "",
            "listInfo": list_info,
            "discNote": disc_note,
            "candidates": candidates,
            "listAgrees": list_offset is not None and list_offset in candidates,
            "proposed": proposed,
            "notes": list(report.notes),
        }
        self._set_step("Done")
        self._running = False
        self.changed.emit()

    @Slot(int)
    def setOffset(self, offset: int) -> None:
        self._offset = offset
        self.changed.emit()

    @Slot(result=bool)
    def save(self) -> bool:
        b = self.backend
        rec, report = b.record, self._report
        if rec is None or report is None:
            return False
        rec.profile = report.apply(rec.profile)
        disc = b.disc
        if disc is not None and self._candidates:
            rec.observe_offsets(ar_disc_id(disc.toc).filename, self._candidates)
        chosen = self._offset if self._offset is not None else rec.profile.read_offset
        if rec.offset_source is not OffsetSource.DISC or chosen != rec.profile.read_offset:
            if chosen == self._list_offset:
                source = OffsetSource.DRIVE_LIST
            elif len(self._candidates) == 1 and chosen == self._candidates[0]:
                source = OffsetSource.DISC
            else:
                source = OffsetSource.MANUAL
            rec.set_offset(chosen, source)
        rec.profile = replace(rec.profile)
        rec.features_tested = dt.date.today().isoformat()
        rec.notes = list(report.notes)
        try:
            b.store.save()
        except OSError as exc:
            self._error = f"Cannot save: {exc}"
            self.changed.emit()
            return False
        b.apply_drive_record(rec)
        return True
