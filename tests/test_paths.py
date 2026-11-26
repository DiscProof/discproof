from __future__ import annotations

from pathlib import Path

import pytest

from discproof.config import paths


def test_xdg_env_vars_are_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    assert paths.config_dir() == tmp_path / "c" / "discproof"
    assert paths.data_dir() == tmp_path / "d" / "discproof"
    assert paths.cache_dir() == tmp_path / "k" / "discproof"


def test_defaults_and_relative_values_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")  # invalid per spec
    assert paths.config_dir() == tmp_path / ".config" / "discproof"
    assert paths.data_dir() == tmp_path / ".local/share" / "discproof"


def test_ensure_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    paths.ensure_dirs()
    paths.ensure_dirs()  # idempotent
    assert paths.config_dir().is_dir()
    assert paths.data_dir().is_dir()
    assert paths.cache_dir().is_dir()


def test_legacy_folders_move_to_new_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    old = tmp_path / "XDG_CONFIG_HOME" / "perfect-audio-copy"
    old.mkdir(parents=True)
    (old / "drives.toml").write_text("x")
    assert paths.migrate_legacy_dirs() == [paths.config_dir()]
    assert (paths.config_dir() / "drives.toml").read_text() == "x"
    assert not old.exists()
    assert paths.migrate_legacy_dirs() == []  # second run is a no-op


def test_legacy_folder_never_overwrites_new(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    old = tmp_path / "XDG_DATA_HOME" / "perfect-audio-copy"
    old.mkdir(parents=True)
    paths.data_dir().mkdir(parents=True)
    (old / "metadata.sqlite").write_text("old")
    assert paths.migrate_legacy_dirs() == []
    assert (old / "metadata.sqlite").read_text() == "old"
    assert not (paths.data_dir() / "metadata.sqlite").exists()
