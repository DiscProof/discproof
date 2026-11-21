"""Drive enumeration via sysfs and udev event classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from perfect_audio_copy.drive.enumerate import list_drives, max_transfer_bytes
from perfect_audio_copy.drive.monitor import DiscEventKind, classify_event


def make_sysfs(root: Path, name: str, vendor: str, model: str, kb: str = "512") -> None:
    dev = root / "sys" / "class" / "block" / name
    (dev / "device").mkdir(parents=True)
    (dev / "queue").mkdir()
    (dev / "device" / "vendor").write_text(vendor + "  \n")
    (dev / "device" / "model").write_text(model + "\n")
    (dev / "device" / "rev").write_text("1.00\n")
    (dev / "queue" / "max_sectors_kb").write_text(kb + "\n")
    (root / "dev").mkdir(exist_ok=True)
    (root / "dev" / name).write_text("")


def test_list_drives(tmp_path: Path) -> None:
    make_sysfs(tmp_path, "sr10", "ASUS", "BW-16D1HT")
    make_sysfs(tmp_path, "sr2", "PLEXTOR", "DVDR PX-760A", kb="120")
    (tmp_path / "sys" / "class" / "block" / "sda").mkdir()
    drives = list_drives(str(tmp_path / "sys"), str(tmp_path / "dev"))
    assert [d.kernel_name for d in drives] == ["sr2", "sr10"]  # natural order
    assert drives[0].name == "PLEXTOR DVDR PX-760A"
    assert drives[0].max_transfer_bytes == 120 * 1024
    assert drives[0].readable


def test_list_drives_without_sysfs(tmp_path: Path) -> None:
    assert list_drives(str(tmp_path / "nope")) == []


def test_max_transfer_unknown(tmp_path: Path) -> None:
    assert max_transfer_bytes("/dev/sr0", str(tmp_path)) is None


BASE = {"DEVNAME": "/dev/sr0", "ID_CDROM": "1"}


@pytest.mark.parametrize(
    ("action", "props", "kind", "tracks"),
    [
        ("add", {}, DiscEventKind.DRIVE_ADDED, 0),
        ("remove", {}, DiscEventKind.DRIVE_REMOVED, 0),
        (
            "change",
            {"ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": "12"},
            DiscEventKind.AUDIO_DISC_INSERTED,
            12,
        ),
        (
            "change",
            {"ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "1"},
            DiscEventKind.OTHER_DISC_INSERTED,
            0,
        ),
        ("change", {}, DiscEventKind.DISC_REMOVED, 0),
    ],
)
def test_classify_event(
    action: str, props: dict[str, str], kind: DiscEventKind, tracks: int
) -> None:
    event = classify_event(action, {**BASE, **props})
    assert event is not None
    assert event.kind is kind
    assert event.path == "/dev/sr0"
    assert event.audio_tracks == tracks


def test_classify_ignores_non_optical() -> None:
    assert classify_event("change", {"DEVNAME": "/dev/sda"}) is None
    assert classify_event("bind", BASE) is None
