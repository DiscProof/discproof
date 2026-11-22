"""Per-drive profiles, stored in ``drives.toml`` in the config directory and keyed by
vendor + model + firmware (as INQUIRY reports them)."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any

from perfect_audio_copy.config import paths
from perfect_audio_copy.config.tomlw import dumps
from perfect_audio_copy.engine.profile import CacheDefeat, DriveProfile

#: Distinct discs that must agree before a disc-detected offset is adopted (as EAC).
OFFSET_CONFIRMATIONS = 2


class OffsetSource(Enum):
    DEFAULT = "default"  # nothing known: 0
    DRIVE_LIST = "accuraterip-list"
    DISC = "disc"  # confirmed by OFFSET_CONFIRMATIONS discs
    MANUAL = "manual"


@dataclass(slots=True)
class DriveRecord:
    vendor: str
    model: str
    firmware: str
    profile: DriveProfile = field(default_factory=DriveProfile)
    offset_source: OffsetSource = OffsetSource.DEFAULT
    #: disc ID -> offsets that disc supported (several if it has pressings that
    #: differ by an offset)
    offset_observations: dict[str, list[int]] = field(default_factory=dict)
    features_tested: str | None = None  # ISO date
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return drive_key(self.vendor, self.model, self.firmware)

    def observe_offsets(self, disc_id: str, offsets: list[int]) -> bool:
        """Record the candidate offsets a disc supported. An offset is adopted (returns
        True) once it is the only one that OFFSET_CONFIRMATIONS different discs share,
        unless the offset was set manually."""
        if not offsets:
            return False
        self.offset_observations[disc_id] = sorted(set(offsets))
        confirmed = self.confirmed_offset()
        if confirmed is None or self.offset_source is OffsetSource.MANUAL:
            return False
        changed = (
            self.profile.read_offset != confirmed or self.offset_source is not OffsetSource.DISC
        )
        self.profile = replace(self.profile, read_offset=confirmed)
        self.offset_source = OffsetSource.DISC
        return changed

    def confirmed_offset(self) -> int | None:
        votes: dict[int, int] = {}
        for offs in self.offset_observations.values():
            for o in offs:
                votes[o] = votes.get(o, 0) + 1
        agreed = [o for o, n in votes.items() if n >= OFFSET_CONFIRMATIONS]
        return agreed[0] if len(agreed) == 1 else None

    def set_offset(self, offset: int, source: OffsetSource) -> None:
        self.profile = replace(self.profile, read_offset=offset)
        self.offset_source = source


def drive_key(vendor: str, model: str, firmware: str) -> str:
    return " | ".join(" ".join(s.split()) for s in (vendor, model, firmware))


def _profile_to_dict(p: DriveProfile) -> dict[str, Any]:
    d = asdict(p)
    d["cache_defeat"] = p.cache_defeat.value
    return d


def _profile_from_dict(d: dict[str, Any]) -> DriveProfile:
    known = {f.name for f in fields(DriveProfile)}
    kwargs = {k: v for k, v in d.items() if k in known}
    if "cache_defeat" in kwargs:
        kwargs["cache_defeat"] = CacheDefeat(kwargs["cache_defeat"])
    return DriveProfile(**kwargs)


class DriveStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else paths.config_dir() / "drives.toml"
        self.records: dict[str, DriveRecord] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open("rb") as f:
            data = tomllib.load(f)
        for key, d in data.get("drives", {}).items():
            try:
                rec = DriveRecord(
                    vendor=d["vendor"],
                    model=d["model"],
                    firmware=d["firmware"],
                    profile=_profile_from_dict(d.get("profile", {})),
                    offset_source=OffsetSource(d.get("offset_source", "default")),
                    offset_observations={
                        o["disc"]: [int(x) for x in o["offsets"]]
                        for o in d.get("offset_observations", [])
                    },
                    features_tested=d.get("features_tested"),
                    notes=list(d.get("notes", [])),
                )
            except (KeyError, ValueError, TypeError):
                continue  # skip a damaged entry rather than losing every drive
            self.records[key] = rec

    def get(self, vendor: str, model: str, firmware: str) -> DriveRecord | None:
        return self.records.get(drive_key(vendor, model, firmware))

    def get_or_create(self, vendor: str, model: str, firmware: str) -> DriveRecord:
        rec = self.get(vendor, model, firmware)
        if rec is None:
            rec = DriveRecord(vendor.strip(), model.strip(), firmware.strip())
            self.records[rec.key] = rec
        return rec

    def save(self) -> None:
        drives: dict[str, Any] = {}
        for key, r in sorted(self.records.items()):
            drives[key] = {
                "vendor": r.vendor,
                "model": r.model,
                "firmware": r.firmware,
                "offset_source": r.offset_source.value,
                "features_tested": r.features_tested,
                "notes": r.notes,
                "offset_observations": [
                    {"disc": d, "offsets": o} for d, o in sorted(r.offset_observations.items())
                ],
                "profile": _profile_to_dict(r.profile),
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            "# Perfect Audio Copy drive profiles (written by the app).\n\n"
            + dumps({"drives": drives}),
            encoding="utf-8",
        )
        tmp.replace(self.path)
