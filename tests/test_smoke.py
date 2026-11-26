"""Phase 0 smoke tests: packaging, layering rules and the UI launching."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

import discproof

CORE_PACKAGES = ["drive", "engine", "verify", "metadata", "output", "config", "config.paths"]


def test_version_is_set() -> None:
    assert discproof.__version__
    assert discproof.__version__ != "0.0.0+unknown", "package is not installed"


@pytest.mark.parametrize("name", CORE_PACKAGES)
def test_core_packages_do_not_import_qt(name: str) -> None:
    """Core packages must stay Qt-free so they are testable headless and reusable."""
    code = (
        f"import sys, discproof.{name}; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')); "
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{name} imported Qt modules: {proc.stdout.strip()}"


def test_cli_version() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "discproof", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert discproof.__version__ in proc.stdout


def _temp_xdg() -> dict[str, str]:
    """XDG folders in a fresh temp dir, so a launched app never touches the user's own."""
    base = tempfile.mkdtemp(prefix="discproof-xdg-")
    return {
        v: os.path.join(base, v.lower())
        for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")
    }


def _run_smoke(style_override: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", **_temp_xdg()}
    env.pop("QT_QUICK_CONTROLS_STYLE", None)
    if style_override is not None:
        env["QT_QUICK_CONTROLS_STYLE"] = style_override
    return subprocess.run(
        [sys.executable, "-m", "discproof", "--smoke-test"],
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
def test_ui_uses_its_own_themed_style() -> None:
    """The dark theme is drawn from the app's design tokens on top of the Basic style,
    regardless of the desktop's Qt Quick style."""
    pytest.importorskip("PySide6")
    proc = _run_smoke("Fusion")
    assert proc.returncode == 0, proc.stderr
    assert "style=Basic" in proc.stdout


@pytest.mark.ui
def test_demo_window_renders_without_qml_warnings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Load the full window against the demo disc, let the lookup finish, take a
    screenshot; any QML warning (broken binding, missing type) fails the test."""
    pytest.importorskip("PySide6")
    shot = tmp_path / "demo.png"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", **_temp_xdg()}
    proc = subprocess.run(
        [sys.executable, "-m", "discproof", "--demo", "--screenshot", str(shot),
         "--screenshot-after", "2500"],
        capture_output=True, text=True, env=env, timeout=90, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    qml_warnings = [ln for ln in proc.stderr.splitlines() if ".qml:" in ln]
    assert not qml_warnings, "\n".join(qml_warnings)
    assert shot.stat().st_size > 10_000
