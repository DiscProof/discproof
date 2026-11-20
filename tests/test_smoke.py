"""Phase 0 smoke tests: packaging, layering rules and the UI launching."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import perfect_audio_copy

CORE_PACKAGES = ["drive", "engine", "verify", "metadata", "output", "config", "config.paths"]


def test_version_is_set() -> None:
    assert perfect_audio_copy.__version__
    assert perfect_audio_copy.__version__ != "0.0.0+unknown", "package is not installed"


@pytest.mark.parametrize("name", CORE_PACKAGES)
def test_core_packages_do_not_import_qt(name: str) -> None:
    """Core packages must stay Qt-free so they are testable headless and reusable."""
    code = (
        f"import sys, perfect_audio_copy.{name}; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')); "
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{name} imported Qt modules: {proc.stdout.strip()}"


def test_cli_version() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "perfect_audio_copy", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert perfect_audio_copy.__version__ in proc.stdout


def _run_smoke(style_override: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    env.pop("QT_QUICK_CONTROLS_STYLE", None)
    if style_override is not None:
        env["QT_QUICK_CONTROLS_STYLE"] = style_override
    return subprocess.run(
        [sys.executable, "-m", "perfect_audio_copy", "--smoke-test"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


@pytest.mark.ui
def test_ui_launches_offscreen() -> None:
    pytest.importorskip("PySide6")
    proc = _run_smoke()
    assert proc.returncode == 0, proc.stderr
    assert "smoke-test ok" in proc.stdout
    # QML warnings indicate broken bindings/imports even when loading "succeeds".
    qml_warnings = [ln for ln in proc.stderr.splitlines() if ".qml:" in ln]
    assert not qml_warnings, "\n".join(qml_warnings)


@pytest.mark.ui
def test_ui_prefers_plasma_style_when_installed() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QLibraryInfo

    qml_root = QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath)
    has_desktop_style = os.path.isfile(os.path.join(qml_root, "org/kde/desktop/qmldir"))
    proc = _run_smoke()
    assert proc.returncode == 0, proc.stderr
    expected = "org.kde.desktop" if has_desktop_style else "Fusion"
    assert f"style={expected}" in proc.stdout


@pytest.mark.ui
def test_ui_honours_style_env_override() -> None:
    pytest.importorskip("PySide6")
    proc = _run_smoke("Fusion")
    assert proc.returncode == 0, proc.stderr
    assert "style=Fusion" in proc.stdout
