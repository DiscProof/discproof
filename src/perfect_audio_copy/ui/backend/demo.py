"""``--demo``: the real UI and pipeline against a simulated drive, fully offline.

Used for UI tests and screenshots, and to try the app without a CD. The disc has
weak sectors on track 3 (recovered after re-reads) and one bad sector on track 5
(suspicious), and the AccurateRip answer is computed from the true audio.
"""

from __future__ import annotations

import io
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from perfect_audio_copy.config.drives import DriveStore
from perfect_audio_copy.drive.constants import SAMPLES_PER_SECTOR, SECTOR_SIZE
from perfect_audio_copy.drive.device import DiscInfo, SectorRead
from perfect_audio_copy.drive.enumerate import DriveInfo
from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.engine.virtual import VirtualDrive, make_toc
from perfect_audio_copy.metadata.coverart import Cover, prepare_cover
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta
from perfect_audio_copy.metadata.service import LookupResult
from perfect_audio_copy.verify.accuraterip import (
    ArDiscId,
    ArEntry,
    ArResponse,
    ar_checksums,
    ar_disc_id,
)

DEVICE = "/dev/demo0"
TITLES = [
    "Lanterns",
    "Northbound",
    "The Quiet Year",
    "Paper Satellites",
    "Undertow",
    "Halflight",
    "Cold Harbour",
    "Last Light Over the Estuary",
]
SECONDS = [94, 71, 108, 63, 86, 77, 69, 122]
PREGAPS = {2: 150, 4: 150, 6: 75}  # silent INDEX 00 gaps, as on most CDs


@dataclass(frozen=True)
class DemoInquiry:
    vendor: str = "DEMO"
    product: str = "Virtual CD-ROM"
    revision: str = "1.0"


def _disc() -> tuple[bytes, Toc]:
    lengths = [s * 75 for s in SECONDS]
    toc = make_toc(lengths, first_start=0)
    rng = np.random.default_rng(7)
    n = toc.leadout * SAMPLES_PER_SECTOR
    t = np.arange(n) / 44100.0
    tone = 0.25 * np.sin(2 * np.pi * 220 * t) + 0.02 * rng.standard_normal(n)
    left = np.clip(tone * 32767, -32768, 32767).astype("<i2")
    right = np.roll(left, 40)
    pcm = np.empty(2 * n, dtype="<i2")
    pcm[0::2], pcm[1::2] = left, right
    for number, gap in PREGAPS.items():
        start = toc.track(number).start
        pcm[(start - gap) * SAMPLES_PER_SECTOR * 2 : start * SAMPLES_PER_SECTOR * 2] = 0
    return pcm.tobytes(), toc


class DemoDrive:
    """A VirtualDrive plus the drive-session methods the backend uses, slowed down
    to roughly 12x so a rip can be watched."""

    def __init__(self, audio: bytes, toc: Toc, *, seconds_per_sector: float) -> None:
        t3, t5 = toc.track(3).start, toc.track(5).start
        weak = {lba: 0.45 for lba in range(t3 + 900, t3 + 930)}
        self.drive = VirtualDrive(
            audio, toc, weak=weak, bad={t5 + 2000}, pregaps=PREGAPS, q_lag=1, seed=3
        )
        self.toc = toc
        self.delay = seconds_per_sector

    def inquiry(self) -> DemoInquiry:
        return DemoInquiry()

    def wait_ready(self, timeout_s: float = 30.0, poll_s: float = 0.5) -> bool:
        return True

    def read_disc_info(self, *, isrc: bool = True, cdtext: bool = True) -> DiscInfo:
        return DiscInfo(self.toc, mcn="0614427018929")

    def read_toc(self) -> Toc:
        return self.toc

    def read_mcn(self) -> str | None:
        return "0614427018929"

    def read_cdtext(self) -> None:
        return None

    def read_isrc(self, track: int) -> str | None:
        return None

    def read_cd(self, lba: int, count: int, *, c2: bool = False, subq: bool = False) -> SectorRead:
        if self.delay:
            time.sleep(count * self.delay)
        return self.drive.read_cd(lba, count, c2=c2, subq=subq)

    def flush_cache(self, lba: int) -> bool:
        return self.drive.flush_cache(lba)

    def set_speed(self, multiplier: float | None) -> bool:
        return self.drive.set_speed(multiplier)

    def eject(self) -> None:
        pass

    def close(self) -> None:
        pass


def _cover() -> Cover:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    img = Image.new("RGB", (900, 900), (21, 26, 33))
    d = ImageDraw.Draw(img)
    for i in range(18):
        r = 420 - i * 22
        shade = 40 + i * 9
        d.ellipse(
            (450 - r, 450 - r, 450 + r, 450 + r),
            outline=(shade // 3, shade // 2 + 30, shade + 40),
            width=3,
        )
    d.ellipse((410, 410, 490, 490), fill=(76, 154, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return prepare_cover(buf.getvalue())


class DemoMetadata:
    def __init__(self, toc: Toc) -> None:
        self.release = ReleaseMeta(
            Source.MUSICBRAINZ,
            "Estuary Sessions",
            "The Lantern Society",
            [TrackMeta(i + 1, title, "The Lantern Society") for i, title in enumerate(TITLES)],
            release_id="00000000-0000-4000-8000-000000000001",
            date="2019-04-12",
            country="GB",
            label="Northlight Records",
            catalog_number="NLR 042",
            barcode="614427018929",
            media_format="CD",
            exact=True,
        )

    def lookup(self, disc: DiscInfo) -> LookupResult:
        time.sleep(0.4)
        return LookupResult("demo", [self.release], 0)

    def cover(self, release: ReleaseMeta) -> Cover | None:
        time.sleep(0.3)
        return _cover()


class DemoAccurateRip:
    def __init__(self, audio: bytes, toc: Toc) -> None:
        disc = ar_disc_id(toc)
        tracks = toc.audio_tracks
        entries = []
        for t in tracks:
            pcm = audio[t.start * SECTOR_SIZE : toc.end(t.number) * SECTOR_SIZE]
            s = ar_checksums(pcm, first=t is tracks[0], last=t is tracks[-1])
            entries.append(ArEntry(24, s.v1, 0))
        self.responses = [ArResponse(len(tracks), disc.id1, disc.id2, disc.cddb, tuple(entries))]

    def lookup(self, disc: ArDiscId, *, refresh: bool = False) -> list[ArResponse] | None:
        return self.responses


def demo_backend_kwargs(*, speed: float = 12.0) -> dict[str, Any]:
    """Keyword arguments for :class:`Backend` that wire in the demo drive."""
    audio, toc = _disc()
    root = Path(tempfile.mkdtemp(prefix="pac-demo-"))
    settings_file = root / "settings.toml"
    library = (root / "Music").as_posix()
    settings_file.write_text(
        f'[settings]\nlibrary = "{library}"\nnotify = false\neject_when_done = false\n',
        encoding="utf-8",
    )
    session = DemoDrive(audio, toc, seconds_per_sector=1 / (75 * speed) if speed > 0 else 0.0)
    return {
        "drive_factory": lambda path: session,
        "enumerate_drives": lambda: [
            DriveInfo(DEVICE, "demo0", "DEMO", "Virtual CD-ROM", "1.0", None, True, True)
        ],
        "metadata": DemoMetadata(toc),
        "ar_client": DemoAccurateRip(audio, toc),
        "store": DriveStore(root / "drives.toml"),
        "settings_file": settings_file,
        "notifier": lambda title, body: None,
        "offset_list": lambda: [],
    }
