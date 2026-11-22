"""Fixed AlbumReports for the CUE/log golden-file tests."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from perfect_audio_copy.drive.constants import CTRL_COPY_PERMITTED, CTRL_PREEMPHASIS
from perfect_audio_copy.drive.toc import Toc, Track
from perfect_audio_copy.engine.events import TrackResult
from perfect_audio_copy.engine.profile import DriveProfile, ExtractionSettings, Quality
from perfect_audio_copy.metadata.model import ReleaseMeta, Source, TrackMeta
from perfect_audio_copy.output.report import AlbumReport, TrackReport
from perfect_audio_copy.verify.accuraterip import ArChecksums, TrackVerdict
from perfect_audio_copy.verify.offsets import OffsetMatch

TOC = Toc(
    (
        Track(1, 0, 0),
        Track(2, 15312, CTRL_COPY_PERMITTED),
        Track(3, 26904, CTRL_PREEMPHASIS),
    ),
    ((1, 41250),),
)
RELEASE = ReleaseMeta(
    Source.MUSICBRAINZ,
    'Anodyne "Deluxe"',
    "Uncle Tupelo",
    [
        TrackMeta(1, "Slate", "Uncle Tupelo"),
        TrackMeta(2, "Acuff-Rose", "Uncle Tupelo"),
        TrackMeta(3, "The Long Cut", "Uncle Tupelo"),
    ],
    release_id="16f91bd3-af0d-4f20-b9ea-f30deabb05d4",
    date="1993-10-05",
    genre="Alt-Country",
)
CREATED = dt.datetime(2026, 11, 22, 14, 3)


def verdict(n: int, conf: int | None, max_conf: int = 12) -> TrackVerdict:
    sums = ArChecksums(0x284FC705 + n, 0xDC77F9AB + n)
    return TrackVerdict(n, sums, conf, conf, max_conf)


def result(n: int, start: int, sectors: int, **kw: object) -> TrackResult:
    return TrackResult(
        number=n,
        start=start,
        sectors=sectors,
        crc32=0x1A2B3C40 + n,
        peak=32112,
        speed_x=4.25,
        **kw,  # type: ignore[arg-type]
    )


def clean_report() -> AlbumReport:
    tracks = []
    lengths = {1: 15312, 2: 11592, 3: 14346}
    names = {1: "01 - Slate.flac", 2: "02 - Acuff-Rose.flac", 3: "03 - The Long Cut.flac"}
    for t in TOC.tracks:
        n = t.number
        tracks.append(
            TrackReport(
                number=n,
                title=RELEASE.tracks[n - 1].title,
                artist="Uncle Tupelo",
                filename=names[n],
                start=t.start,
                sectors=lengths[n],
                pregap={1: None, 2: 150, 3: 0}[n],
                isrc="USRE10100695" if n == 2 else None,
                preemphasis=t.preemphasis,
                copy_permitted=t.copy_permitted,
                rip=result(n, t.start, lengths[n]),
                ar=verdict(n, 12),
            )
        )
    return AlbumReport(
        app_version="0.1.0",
        created=CREATED,
        drive="HL-DT-ST DVDRAM GP75N (firmware 1.01)",
        profile=DriveProfile(read_offset=6, cache_sectors=256),
        settings=ExtractionSettings(),
        release=RELEASE,
        toc=TOC,
        tracks=tuple(tracks),
        mb_disc_id="ja.BwnUGq1ZK9zJWEzpsQhrOips-",
        cddb_id=0x910D690C,
        ar_disc="dBAR-003-0000a1b2-0001c3d4-910d690c.bin",
        ar_in_database=True,
        mcn="0075994542423",
    )


def troubled_report() -> AlbumReport:
    """Test & Copy, hidden track, a suspicious track, a CRC mismatch, a pressing
    match, an encoding error, multi-disc."""
    base = clean_report()
    t1, t2, t3 = base.tracks
    htoa = TrackReport(
        0,
        "Hidden Track",
        "Uncle Tupelo",
        "00 - Hidden Track.flac",
        0,
        1500,
        rip=result(0, 0, 1500, test_crc32=0x1A2B3C40),
    )
    t1 = replace(t1, start=1500, sectors=13812, rip=result(1, 1500, 13812, test_crc32=0x1A2B3C41))
    t2 = replace(
        t2,
        rip=result(
            2,
            15312,
            11592,
            test_crc32=0xDEADBEEF,
            suspicious=("0:12.34", "0:12.35"),
            recovered_sectors=40,
        ),
        ar=verdict(2, None),
    )
    t3 = replace(
        t3,
        rip=result(3, 26904, 14346, test_crc32=0x1A2B3C43, filled_samples=6),
        ar=verdict(3, None),
        error="flac failed for track 3: disk full",
    )
    toc = Toc(
        (
            Track(1, 1500, 0),
            Track(2, 15312, CTRL_COPY_PERMITTED),
            Track(3, 26904, CTRL_PREEMPHASIS),
        ),
        ((1, 41250),),
    )
    return replace(
        base,
        toc=toc,
        tracks=(htoa, t1, t2, t3),
        settings=ExtractionSettings(test_and_copy=True, quality=Quality.MEDIUM),
        profile=DriveProfile(read_offset=6, caches_audio=False, c2_supported=True, c2_trusted=True),
        release=replace(RELEASE, disc_number=2, disc_total=2, genre=None),
        pressings=(OffsetMatch(-120, (2, 3), 18),),
        mcn=None,
        replaygain=False,
    )


def offline_report() -> AlbumReport:
    """AccurateRip unreachable; metadata typed in by hand; rip cancelled."""
    base = clean_report()
    return replace(
        base,
        tracks=base.tracks[:1],
        ar_in_database=None,
        ar_error="AccurateRip unreachable: no route to host",
        release=replace(RELEASE, source=Source.MANUAL, release_id=None),
        cancelled=True,
    )
