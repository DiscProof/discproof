"""Desktop integration files and the per-user installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from perfect_audio_copy import desktop_install as di


def test_install_and_uninstall(tmp_path: Path) -> None:
    paths = di.install(tmp_path, command="/home/u/.local/bin/perfect-audio-copy")
    assert all(p.exists() for p in paths)
    app = (tmp_path / "applications" / di.APP_FILE).read_text()
    assert "Exec=/home/u/.local/bin/perfect-audio-copy\n" in app
    solid = (tmp_path / "solid" / "actions" / di.SOLID_FILE).read_text()
    assert "Exec=/home/u/.local/bin/perfect-audio-copy\n" in solid
    assert "OpticalDisc.availableContent & 'Audio'" in solid
    assert (tmp_path / "icons/hicolor/scalable/apps" / f"{di.APP_ID}.svg").stat().st_size > 0
    removed = di.uninstall(tmp_path)
    assert sorted(removed) == sorted(paths) and not any(p.exists() for p in paths)


def test_command_with_spaces_is_quoted(tmp_path: Path) -> None:
    di.install(tmp_path, command="/opt/my apps/perfect-audio-copy")
    assert (
        'Exec="/opt/my apps/perfect-audio-copy"'
        in (tmp_path / "applications" / di.APP_FILE).read_text()
    )


@pytest.mark.skipif(not shutil.which("desktop-file-validate"), reason="desktop-file-utils missing")
def test_launcher_is_valid() -> None:
    proc = subprocess.run(
        ["desktop-file-validate", str(di.DESKTOP_DIR / di.APP_FILE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not shutil.which("appstreamcli"), reason="appstream missing")
def test_metainfo_is_valid() -> None:
    proc = subprocess.run(
        [
            "appstreamcli",
            "validate",
            "--no-net",
            "--format=yaml",
            str(di.DESKTOP_DIR / di.METAINFO_FILE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    tags = [
        ln.split(":", 1)[1].strip()
        for ln in proc.stdout.splitlines()
        if ln.strip().startswith("- tag:")
    ]
    assert tags == [], proc.stdout
