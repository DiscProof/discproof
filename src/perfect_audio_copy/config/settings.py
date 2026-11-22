"""Application settings, stored in ``settings.toml`` in the config directory.

Unknown keys are ignored and missing keys take their defaults, so the file survives
upgrades in both directions.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from perfect_audio_copy.config import paths
from perfect_audio_copy.config.tomlw import dumps

MODES = ("secure", "burst")
QUALITIES = ("low", "medium", "high")
EXISTING = ("ask", "overwrite", "suffix", "skip")
HTOA = ("discard", "track00")


@dataclass(frozen=True, slots=True)
class AppSettings:
    library: str = str(Path.home() / "Music")
    template: str = "{albumartist}/{album} ({year})/{disc_prefix}{tracknumber:02} - {title}"
    disc_subfolders: bool = True  # multi-disc sets: "Disc 1", "Disc 2" subfolders
    mode: str = "secure"
    test_and_copy: bool = False
    quality: str = "high"
    flac_level: int = 8
    replaygain: bool = True
    use_musicbrainz: bool = True
    use_discogs: bool = True
    discogs_token: str = ""
    htoa: str = "discard"  # rip the hidden track before track 1 as track 00?
    detect_gaps: bool = True  # find pre-gaps (CUE INDEX 00, log) before ripping
    existing: str = "ask"
    eject_when_done: bool = True
    auto_lookup: bool = True
    auto_rip: bool = False  # rip without asking when metadata is unambiguous
    notify: bool = True
    last_drive: str = ""

    def validated(self) -> AppSettings:
        """Clamp/replace invalid values with defaults (hand-edited files)."""
        d = AppSettings()
        return replace(
            self,
            mode=self.mode if self.mode in MODES else d.mode,
            quality=self.quality if self.quality in QUALITIES else d.quality,
            existing=self.existing if self.existing in EXISTING else d.existing,
            htoa=self.htoa if self.htoa in HTOA else d.htoa,
            flac_level=min(8, max(0, int(self.flac_level))),
            template=self.template.strip() or d.template,
            library=self.library.strip() or d.library,
        )


def settings_path() -> Path:
    return paths.config_dir() / "settings.toml"


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or settings_path()
    if not path.exists():
        return AppSettings()
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f).get("settings", {})
    except (OSError, tomllib.TOMLDecodeError):
        return AppSettings()
    defaults = AppSettings()
    kwargs: dict[str, Any] = {}
    for fl in fields(AppSettings):
        if fl.name not in data:
            continue
        value = data[fl.name]
        default = getattr(defaults, fl.name)
        if isinstance(default, bool):
            if isinstance(value, bool):
                kwargs[fl.name] = value
        elif isinstance(default, int):
            if isinstance(value, int) and not isinstance(value, bool):
                kwargs[fl.name] = value
        elif isinstance(value, str):
            kwargs[fl.name] = value
    return AppSettings(**kwargs).validated()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "# Perfect Audio Copy settings (written by the app).\n\n"
        + dumps({"settings": asdict(settings)}),
        encoding="utf-8",
    )
    tmp.replace(path)
