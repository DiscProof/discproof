"""The QML-facing Backend, driven through the offline demo disc (no window)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import wait_until

pytestmark = pytest.mark.ui


@pytest.fixture
def backend(qapp: object, tmp_path: Path) -> Any:
    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs

    kw = demo_backend_kwargs(speed=0)
    b = Backend(**kw)
    b.refreshDrives()
    wait_until(lambda: b.property("state") == "ready" and b.property("coverUrl") != "")
    return b


def prop(b: Any, name: str) -> Any:
    return b.property(name)


def test_disc_loads_and_is_identified(backend: Any) -> None:
    album = prop(backend, "album")
    assert album["present"] and album["title"] == "Estuary Sessions" and album["exact"]
    assert prop(backend, "message") == "Exact match"
    assert prop(backend, "discPresent")
    model = backend.tracks
    assert model.rowCount() == 8
    assert [r.title for r in model.rows][:2] == ["Lanterns", "Northbound"]
    assert all(r.status == "pending" and r.selected for r in model.rows)
    assert prop(backend, "coverInfo") == "900 \u00d7 900"
    profile = prop(backend, "driveProfile")
    assert profile["known"] and not profile["configured"]


def test_full_rip_reports_results(backend: Any) -> None:
    backend.startRip("")
    assert prop(backend, "state") == "ripping"
    wait_until(lambda: prop(backend, "state") == "done", timeout=120)
    res = prop(backend, "results")
    assert res["visible"] and res["total"] == 8
    assert res["accurate"] == 7 and res["suspicious"] == 1  # demo: bad sector on track 5
    assert res["headline"] == "Finished with errors" and res["tone"] == "negative"
    rows = {r.number: r for r in backend.tracks.rows}
    assert rows[5].status == "suspicious"
    assert all(
        rows[n].status == "accurate" and rows[n].confidence == 24 for n in (1, 2, 3, 4, 6, 7, 8)
    )
    assert all(len(r.crc) == 8 for r in rows.values())
    folder = Path(res["folder"])
    assert len(list(folder.glob("*.flac"))) == 8 and (folder / "cover.jpg").exists()
    log = backend.logText()
    assert "Suspicious position" in log
    # Gaps (demo: 2 s before tracks 2 and 4, 1 s before 6; Q lags a sector).
    assert (rows[2].pregap, rows[3].pregap, rows[6].pregap) == ("0:02.00", "—", "0:01.00")
    assert "Pre-gap length  0:02.00 (silent)" in log
    cue = next(folder.glob("*.cue")).read_text()
    assert cue.count("INDEX 00") == 3
    # Sector map: track 3 has recovered sectors, track 5 a suspicious one.
    _, _, rec3, sus3 = backend.sector_maps.counts(3)
    _, _, _, sus5 = backend.sector_maps.counts(5)
    assert rec3 > 0 and sus3 == 0 and sus5 >= 1
    img = backend.sector_maps.render(5, 400, 3)
    assert img.width() == 400


def test_rip_only_selected_tracks(backend: Any) -> None:
    backend.selectAll(False)
    backend.setTrackSelected(2, True)
    backend.startRip("burst")
    wait_until(lambda: prop(backend, "state") == "done", timeout=60)
    res = prop(backend, "results")
    assert res["total"] == 1 and res["accurate"] == 1
    assert [r.status for r in backend.tracks.rows if r.number != 2] == ["pending"] * 7


def test_nothing_selected(backend: Any) -> None:
    backend.selectAll(False)
    backend.startRip("")
    assert prop(backend, "state") == "ready"
    assert prop(backend, "message") == "Select at least one track"


def test_editing_album_and_tracks(backend: Any) -> None:
    backend.setAlbumField("title", "  Estuary (Remaster) ")
    backend.setAlbumField("artist", "Lantern Society")
    backend.setAlbumField("discTotal", "2")
    backend.setAlbumField("discNumber", "x")  # ignored
    backend.setTrackField(1, "title", "Lanterns (Intro)")
    album = prop(backend, "album")
    assert (album["title"], album["artist"], album["discTotal"], album["discNumber"]) == (
        "Estuary (Remaster)",
        "Lantern Society",
        2,
        1,
    )
    rows = backend.tracks.rows
    assert rows[0].title == "Lanterns (Intro)" and rows[1].artist == "Lantern Society"
    tpl = "{albumartist}/{album}/{disc_prefix}{tracknumber:02} - {title}"
    # Multi-disc (disc 1 of 2): a "Disc 1" subfolder by default, "1-01" names if off.
    assert backend.templatePreview(tpl) == (
        "Lantern Society/Estuary (Remaster)/Disc 1/01 - Lanterns (Intro).flac"
    )
    backend.setSetting("disc_subfolders", False)
    assert backend.templatePreview(tpl) == (
        "Lantern Society/Estuary (Remaster)/1-01 - Lanterns (Intro).flac"
    )
    assert backend.templatePreview("{nope}").startswith("⚠")


def test_folder_conflict_asks_then_resolves(backend: Any) -> None:
    backend.startRip("burst")
    wait_until(lambda: prop(backend, "state") == "done", timeout=60)
    first = Path(prop(backend, "results")["folder"])
    asked: list[str] = []
    backend.folderConflict.connect(asked.append)
    backend.startRip("burst")
    assert asked == [str(first)] and prop(backend, "state") == "done"
    backend.resolveConflict("suffix")
    wait_until(
        lambda: (
            prop(backend, "state") == "done" and prop(backend, "results")["folder"] != str(first)
        ),
        timeout=60,
    )
    assert prop(backend, "results")["folder"].endswith("(2)")


def test_settings_persist(backend: Any) -> None:
    backend.setSetting("quality", "medium")
    backend.setSetting("flac_level", 5)
    backend.setSetting("quality", "bogus")  # rejected by validation
    backend.setSetting("no_such_key", 1)
    s = prop(backend, "settings")
    assert s["quality"] == "high"  # the invalid value was replaced by the default
    from perfect_audio_copy.config.settings import load_settings

    saved = load_settings(backend._settings_file)
    assert saved.flac_level == 5 and saved.quality == "high"  # "bogus" fell back to default


def test_release_choice(backend: Any) -> None:
    from dataclasses import replace

    first = backend._releases[0]
    second = replace(
        first, title="Estuary Sessions (Deluxe)", exact=False, tracks=list(first.tracks)
    )
    asked: list[bool] = []
    backend.chooseReleaseRequested.connect(lambda: asked.append(True))
    backend._lookup_done(backend._generation, [first, second], None, [])
    assert prop(backend, "needsChoice") and asked
    backend.chooseRelease(1)
    assert prop(backend, "album")["title"] == "Estuary Sessions (Deluxe)"
    assert not prop(backend, "needsChoice")


def test_drive_wizard_saves_profile(backend: Any) -> None:
    from perfect_audio_copy.ui.backend.wizard import DriveWizard

    w = DriveWizard(backend)
    w.start()
    wait_until(
        lambda: w.property("status")["hasResult"] or w.property("status")["error"], timeout=60
    )
    st = w.property("status")
    assert st["hasResult"], st["error"]
    w.setOffset(6)
    assert w.save()
    profile = prop(backend, "driveProfile")
    assert profile["configured"] and profile["offset"] == 6 and profile["offsetSource"] == "manual"
    rec = backend.store.get("DEMO", "Virtual CD-ROM", "1.0")
    assert rec is not None and rec.features_tested


def test_eject_keeps_results_until_next_disc(backend: Any) -> None:
    backend.startRip("burst")
    wait_until(lambda: prop(backend, "state") == "done", timeout=60)
    backend._ejected()
    assert prop(backend, "state") == "done" and prop(backend, "results")["visible"]
    assert not prop(backend, "discPresent")
    backend.loadDisc()
    wait_until(
        lambda: prop(backend, "state") == "ready" and not prop(backend, "results")["visible"]
    )


def test_sector_map_widens_single_bad_sector(qapp: object) -> None:
    from perfect_audio_copy.engine.events import SectorStatus
    from perfect_audio_copy.ui.backend.images import COLORS, SUSPICIOUS, SectorMaps

    maps = SectorMaps()
    maps.reset([(1, 0, 10000)])
    maps.mark(0, [SectorStatus.OK] * 10000)
    maps.mark(5000, [SectorStatus.SUSPICIOUS])
    img = maps.render(1, 200, 2)
    red = [
        x for x in range(200) if img.pixelColor(x, 0).name().upper() == COLORS[SUSPICIOUS].upper()
    ]
    assert len(red) >= 3 and 98 <= red[0] <= 100


def slow_backend(tmp_path: Path, *, isrcs: dict[int, str], cdtext=None, online: bool = True):  # type: ignore[no-untyped-def]
    import time as _time

    from perfect_audio_copy.metadata.service import LookupResult
    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs

    kw = demo_backend_kwargs(speed=0)
    session = kw["drive_factory"]("x")
    calls: list[int] = []

    def read_isrc(track: int) -> str | None:
        calls.append(track)
        _time.sleep(0.05)
        return isrcs.get(track)

    def read_cdtext():  # type: ignore[no-untyped-def]
        _time.sleep(0.3)
        return cdtext

    session.read_isrc = read_isrc
    session.read_cdtext = read_cdtext
    if not online:
        from perfect_audio_copy.metadata.service import blank_release

        kw["metadata"].lookup = lambda disc: LookupResult("x", [blank_release(disc.toc)], 0)
        kw["metadata"].cover = lambda release: None
    b = Backend(**kw)
    return b, calls


def test_disc_shows_before_slow_reads_finish(qapp: object, tmp_path: Path) -> None:
    b, calls = slow_backend(
        tmp_path, isrcs={1: "GBAAA0100001", 2: "GBAAA0100002", 5: "GBAAA0100005"}
    )
    b.refreshDrives()
    wait_until(lambda: b.property("album").get("present") is True, timeout=5)
    assert calls == []  # ISRCs not read yet: CD-Text comes first and takes 0.3 s
    wait_until(lambda: len(calls) == 8 and b.disc is not None and 5 in b.disc.isrcs)
    wait_until(
        lambda: b._release is not None and b._release.tracks[4].isrcs[:1] == ("GBAAA0100005",)
    )


def test_isrc_probe_stops_when_first_tracks_have_none(qapp: object, tmp_path: Path) -> None:
    b, calls = slow_backend(tmp_path, isrcs={})
    b.refreshDrives()
    wait_until(lambda: b.property("state") == "ready" and len(calls) >= 2)
    import time as _time

    _time.sleep(0.4)
    assert calls == [1, 2]


def test_late_cdtext_used_when_not_found_online(qapp: object, tmp_path: Path) -> None:
    from perfect_audio_copy.drive.cdtext import CdText, CdTextEntry

    text = CdText(
        album=CdTextEntry(title="From CD-Text", performer="Someone"),
        tracks={1: CdTextEntry(title="One")},
    )
    b, _ = slow_backend(tmp_path, isrcs={}, cdtext=text, online=False)
    b.refreshDrives()
    wait_until(lambda: b.property("album").get("title") == "From CD-Text", timeout=10)
    assert b.property("album")["source"] == "CD-Text"
    assert b.tracks.rows[0].title == "One"


def test_unconfigured_drive_uses_drive_list_offset(qapp: object, tmp_path: Path) -> None:
    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs
    from perfect_audio_copy.verify.offsets import DriveOffsetEntry

    kw = demo_backend_kwargs(speed=0)
    kw["offset_list"] = lambda: [DriveOffsetEntry("DEMO", "Virtual CD-ROM", 6, 99, 100)]
    b = Backend(**kw)
    b.refreshDrives()
    wait_until(lambda: b.property("driveProfile").get("offsetSource") == "accuraterip-list")
    profile = b.property("driveProfile")
    assert profile["offset"] == 6 and not profile["configured"]
    rec = b.store.get("DEMO", "Virtual CD-ROM", "1.0")
    assert rec is not None and rec.profile.read_offset == 6  # saved


def test_cancel_during_gap_detection(qapp: object, tmp_path: Path) -> None:
    import time as _time

    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs

    kw = demo_backend_kwargs(speed=0)
    session = kw["drive_factory"]("x")
    real = session.read_cd

    def slow(lba, count, **k):  # type: ignore[no-untyped-def]
        if k.get("subq"):
            _time.sleep(0.02)  # make gap detection take a while
        return real(lba, count, **k)

    session.read_cd = slow
    b = Backend(**kw)
    b.refreshDrives()
    wait_until(lambda: b.property("state") == "ready" and b.property("coverUrl") != "")
    b.startRip("")
    wait_until(lambda: b.property("rip").get("pass") == "gaps")
    b.cancelRip()
    wait_until(lambda: b.property("state") == "ready")
    assert b.property("message") == "Rip cancelled"
    assert not b.property("results")["visible"]


@pytest.mark.parametrize(("level", "warned"), [(0, False), (20, False), (9000, True)])
def test_hidden_track_warning_only_for_real_audio(qapp: object, level: int, warned: bool) -> None:
    import threading

    from perfect_audio_copy.drive.device import DiscInfo
    from perfect_audio_copy.engine.gaps import GapResult
    from perfect_audio_copy.engine.profile import ExtractionSettings
    from perfect_audio_copy.engine.virtual import make_toc
    from perfect_audio_copy.ui.backend.controller import Backend
    from perfect_audio_copy.ui.backend.demo import demo_backend_kwargs

    b = Backend(**demo_backend_kwargs(speed=0))
    toc = make_toc([300, 300], first_start=40)

    class FakeDetector:
        def __init__(self, *a, **k):  # type: ignore[no-untyped-def]
            pass

        def detect(self, n: int) -> GapResult:
            return GapResult(n, 40 if n == 1 else 0, True, level if n == 1 else None)

    import perfect_audio_copy.ui.backend.controller as ctl

    real = ctl.GapDetector
    ctl.GapDetector = FakeDetector  # type: ignore[misc,assignment]
    try:
        _, peaks, notes = b._detect_gaps(
            None,
            DiscInfo(toc),
            ExtractionSettings(tracks=(1, 2)),
            b._generation,
            threading.Event(),
        )
    finally:
        ctl.GapDetector = real  # type: ignore[misc]
    assert peaks[1] == level
    assert any("hidden track" in n for n in notes) is warned
