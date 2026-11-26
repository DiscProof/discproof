"""Desktop integration for per-user installs (``uv tool install`` / ``pipx``), which
do not install launchers: ``discproof --install-desktop``.

Copies the launcher, icon, AppStream metadata and the Plasma "audio CD inserted"
action into ``$XDG_DATA_HOME`` (``~/.local/share``), pointing them at the installed
command. The RPM installs the same files system-wide instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from discproof import APP_ID

PKG = Path(__file__).resolve().parent
DESKTOP_DIR = PKG / "ui" / "desktop"
ICON = PKG / "ui" / "icons" / f"{APP_ID}.svg"
APP_FILE = f"{APP_ID}.desktop"
SOLID_FILE = "discproof-rip-audio-cd.desktop"
METAINFO_FILE = f"{APP_ID}.metainfo.xml"


def data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".local" / "share"


def _targets(base: Path) -> dict[str, Path]:
    return {
        "app": base / "applications" / APP_FILE,
        "solid": base / "solid" / "actions" / SOLID_FILE,
        "metainfo": base / "metainfo" / METAINFO_FILE,
        "icon": base / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
    }


def command_path() -> str:
    """Absolute path of the ``discproof`` command being run (so a pipx/uv
    install points at itself, even if another copy comes first on PATH). Symlinks
    are kept: pipx's ``~/.local/bin`` link survives reinstalls."""
    argv0 = Path(sys.argv[0])
    if argv0.name == "discproof":
        return os.path.abspath(argv0)
    return shutil.which("discproof") or "discproof"


def _with_exec(text: str, command: str) -> str:
    quoted = f'"{command}"' if " " in command else command
    return (
        "\n".join(
            f"Exec={quoted}" if line.startswith("Exec=") else line for line in text.splitlines()
        )
        + "\n"
    )


def install(base: Path | None = None, command: str | None = None) -> list[Path]:
    base = base or data_home()
    command = command or command_path()
    t = _targets(base)
    for path in t.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    t["app"].write_text(
        _with_exec((DESKTOP_DIR / APP_FILE).read_text(encoding="utf-8"), command), "utf-8"
    )
    t["solid"].write_text(
        _with_exec((DESKTOP_DIR / SOLID_FILE).read_text(encoding="utf-8"), command), "utf-8"
    )
    shutil.copyfile(DESKTOP_DIR / METAINFO_FILE, t["metainfo"])
    shutil.copyfile(ICON, t["icon"])
    _refresh(base)
    return list(t.values())


def uninstall(base: Path | None = None) -> list[Path]:
    removed = []
    for path in _targets(base or data_home()).values():
        if path.exists():
            path.unlink()
            removed.append(path)
    _refresh(base or data_home())
    return removed


def _refresh(base: Path) -> None:
    """Let the desktop notice the change (best effort; the tools may be missing)."""
    for cmd in (
        ["update-desktop-database", str(base / "applications")],
        ["gtk-update-icon-cache", "-qtf", str(base / "icons" / "hicolor")],
        ["kbuildsycoca6"],
    ):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True, check=False, timeout=60)
