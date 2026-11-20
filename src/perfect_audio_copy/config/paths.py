"""XDG Base Directory locations for settings, data and cache.

- settings and per-drive profiles: ``$XDG_CONFIG_HOME/perfect-audio-copy`` (~/.config/...)
- metadata DB, AccurateRip drive list: ``$XDG_DATA_HOME/perfect-audio-copy`` (~/.local/share/...)
- disposable downloads (cover art, AR responses): ``$XDG_CACHE_HOME/perfect-audio-copy``
"""

from __future__ import annotations

import os
from pathlib import Path

from perfect_audio_copy import APP_SLUG


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    # The spec says relative paths are invalid and must be ignored.
    base = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    return base / APP_SLUG


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
