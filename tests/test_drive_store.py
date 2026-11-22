"""Per-drive profiles in drives.toml, and the TOML writer."""

from __future__ import annotations

import tomllib
from pathlib import Path

from perfect_audio_copy.config.drives import DriveStore, OffsetSource, drive_key
from perfect_audio_copy.config.tomlw import dumps
from perfect_audio_copy.engine.profile import CacheDefeat


def test_tomlw_roundtrip() -> None:
    data = {
        "top": 1,
        "drives": {
            "HL-DT-ST | DVDRAM GP75N | 1.01": {
                "name": 'quote " and \\ backslash, ünïcode',
                "flag": True,
                "ratio": 2.5,
                "list": [1, 2, 3],
                "obs": [{"disc": "a", "offset": 6}, {"disc": "b", "offset": -12}],
                "profile": {"read_offset": 6, "cache_defeat": "seek-away"},
            }
        },
    }
    assert tomllib.loads(dumps(data)) == data


def test_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "drives.toml"
    store = DriveStore(path)
    rec = store.get_or_create("HL-DT-ST", "DVDRAM GP75N", "1.01")
    rec.set_offset(6, OffsetSource.DRIVE_LIST)
    from dataclasses import replace

    rec.profile = replace(
        rec.profile, caches_audio=True, cache_defeat=CacheDefeat.SEEK_AWAY, cache_sectors=1024
    )
    rec.features_tested = "2026-11-22"
    rec.notes.append("FUA ignored")
    rec.observe_offsets("dBAR-021-x", [6])
    store.save()

    again = DriveStore(path).get("HL-DT-ST", "DVDRAM  GP75N", "1.01")
    assert again is not None
    assert again.profile == rec.profile
    assert again.offset_source is OffsetSource.DRIVE_LIST
    assert again.offset_observations == {"dBAR-021-x": [6]}
    assert again.notes == ["FUA ignored"] and again.features_tested == "2026-11-22"
    assert drive_key(" HL-DT-ST ", "DVDRAM GP75N", "1.01") == again.key


def test_offset_needs_two_agreeing_discs(tmp_path: Path) -> None:
    rec = DriveStore(tmp_path / "d.toml").get_or_create("V", "M", "1")
    assert not rec.observe_offsets("disc-a", [667])
    assert rec.profile.read_offset == 0
    assert not rec.observe_offsets("disc-b", [670])  # disagrees
    assert rec.observe_offsets("disc-c", [667])
    assert rec.profile.read_offset == 667 and rec.offset_source is OffsetSource.DISC
    assert not rec.observe_offsets("disc-c", [667])  # nothing new


def test_ambiguous_discs_are_resolved_by_intersection(tmp_path: Path) -> None:
    """Like the real GP75N test disc: three pressings, offsets +652, -146, +6."""
    rec = DriveStore(tmp_path / "d.toml").get_or_create("V", "M", "1")
    assert not rec.observe_offsets("multi-pressing", [652, -146, 6])
    assert rec.confirmed_offset() is None
    assert rec.observe_offsets("other-disc", [6, 120])
    assert rec.profile.read_offset == 6


def test_two_ambiguous_discs_sharing_two_offsets_do_not_confirm(tmp_path: Path) -> None:
    rec = DriveStore(tmp_path / "d.toml").get_or_create("V", "M", "1")
    rec.observe_offsets("a", [6, 652])
    assert not rec.observe_offsets("b", [6, 652])
    assert rec.confirmed_offset() is None


def test_manual_offset_is_never_overridden(tmp_path: Path) -> None:
    rec = DriveStore(tmp_path / "d.toml").get_or_create("V", "M", "1")
    rec.set_offset(30, OffsetSource.MANUAL)
    rec.observe_offsets("a", [6])
    rec.observe_offsets("b", [6])
    assert rec.profile.read_offset == 30


def test_damaged_entry_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "drives.toml"
    path.write_text(
        '[drives."bad"]\nvendor = "X"\n\n'
        '[drives."good"]\nvendor = "V"\nmodel = "M"\nfirmware = "1"\nfuture_field = 3\n'
        '[drives."good".profile]\nread_offset = 12\nunknown = true\n'
    )
    store = DriveStore(path)
    assert list(store.records) == ["good"]
    assert store.records["good"].profile.read_offset == 12
