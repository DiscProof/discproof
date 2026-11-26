"""XDG Base Directory locations for settings, data and cache.

- settings and per-drive profiles: ``$XDG_CONFIG_HOME/discproof`` (~/.config/...)
- metadata DB, AccurateRip drive list: ``$XDG_DATA_HOME/discproof`` (~/.local/share/...)
- disposable downloads (cover art, AR responses): ``$XDG_CACHE_HOME/discproof``

The app was called Perfect Audio Copy: a folder still under the old name
(``perfect-audio-copy``) is moved to the new name by :func:`migrate_legacy_dirs`,
which the commands call at startup, unless the new folder already exists.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from discproof import APP_SLUG

LEGACY_SLUG = "perfect-audio-copy"
log = logging.getLogger(__name__)


_BASES = (
    ("XDG_CONFIG_HOME", ".config"),
    ("XDG_DATA_HOME", ".local/share"),
    ("XDG_CACHE_HOME", ".cache"),
)


def _base(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    # The spec says relative paths are invalid and must be ignored.
    return Path(value) if value and os.path.isabs(value) else Path.home() / fallback


def _xdg(var: str, fallback: str) -> Path:
    return _base(var, fallback) / APP_SLUG


def migrate_legacy_dirs() -> list[Path]:
    """Move config, data and cache folders from the old app name. Returns the moved-to paths."""
    moved = []
    for var, fallback in _BASES:
        base = _base(var, fallback)
        if _migrate_legacy(base / LEGACY_SLUG, base / APP_SLUG):
            moved.append(base / APP_SLUG)
    return moved


def _migrate_legacy(old: Path, new: Path) -> bool:
    """Move a folder left under the old app name; never overwrites the new one."""
    if new.exists() or not old.is_dir():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(old, new)
    except OSError:
        log.exception("Could not move %s to %s", old, new)
        return False
    log.info("Moved %s to %s", old, new)
    return True


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def ensure_dirs() -> None:
    """Create all application directories (idempotent)."""
    for d in (config_dir(), data_dir(), cache_dir()):
        d.mkdir(parents=True, exist_ok=True)
