"""Drive offset list, offset detection from a disc, pressing offsets, rip verification."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from discs import drive, noise_disc

from perfect_audio_copy.drive.constants import SECTOR_SIZE
from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.engine.extract import Extractor
from perfect_audio_copy.engine.profile import DriveProfile
from perfect_audio_copy.verify.accuraterip import (
    ArEntry,
    ArResponse,
    ar_checksums,
    ar_disc_id,
)
from perfect_audio_copy.verify.offsets import (
    detected_drive_offset,
    find_drive_offset,
    offset_candidates,
    parse_drive_offsets,
)
from perfect_audio_copy.verify.verify import detect_offset_from_disc, offset_test_tracks, verify_rip

FIXTURE = Path(__file__).parent / "fixtures" / "driveoffsets.htm"
LENGTHS = [200, 150, 120, 180, 160]


def test_parse_drive_offsets() -> None:
    entries = parse_drive_offsets(FIXTURE.read_text())
    assert len(entries) == 8  # header skipped
    compaq = next(e for e in entries if e.vendor == "COMPAQ")
    assert (compaq.model, compaq.offset, compaq.submissions, compaq.agreement) == (
        "CD-ROM LTN403",
        -1164,
        13,
        100,
    )
    purged = next(e for e in entries if e.model == "SBC-06D2X-U")
    assert purged.offset is None
    assert entries[0].vendor == "" and entries[0].model == "16X12 DVD DUAL"


def test_gp75n_found_via_vendor_alias() -> None:
    entries = parse_drive_offsets(FIXTURE.read_text())
    e = find_drive_offset(entries, "HL-DT-ST", "DVDRAM GP75N")
    assert e is not None and e.offset == 6 and e.vendor == "LG Electronics"


def test_lookup_prefers_vendor_then_submissions() -> None:
    entries = parse_drive_offsets(FIXTURE.read_text())
    e = find_drive_offset(entries, "HL-DT-ST", "dvdram   gp65nb60")
    assert e is not None and e.offset == 6 and e.submissions == 1146
    # Model match under another vendor name is still found.
    e = find_drive_offset(entries, "HP", "CDDVDW GP70N")
    assert e is not None and e.offset == 6
    assert find_drive_offset(entries, "ASUS", "SBC-06D2X-U") is None  # purged
    assert find_drive_offset(entries, "NOBODY", "NOTHING") is None


# ---------------------------------------------------------------- AR helpers


def responses_for(audio: bytes, toc: Toc, confidence: int = 9) -> list[ArResponse]:
    """A database answer made from the true audio: one v1 and one v2 response."""
    d = ar_disc_id(toc)
    audio_tracks = toc.audio_tracks
    sums = [
        ar_checksums(
            audio[t.start * SECTOR_SIZE : toc.end(t.number) * SECTOR_SIZE],
            first=t is audio_tracks[0],
            last=t is audio_tracks[-1],
        )
        for t in audio_tracks
    ]
    v1 = ArResponse(
        len(sums), d.id1, d.id2, d.cddb, tuple(ArEntry(confidence, s.v1, 0) for s in sums)
    )
    v2 = ArResponse(
        len(sums), d.id1, d.id2, d.cddb, tuple(ArEntry(confidence - 2, s.v2, 0) for s in sums)
    )
    return [v1, v2]


def shifted(audio: bytes, samples: int) -> bytes:
    """Another pressing: the same music ``samples`` later on the disc."""
    x = np.frombuffer(audio, dtype="<u4")
    out = np.zeros_like(x)
    if samples >= 0:
        out[samples:] = x[: len(x) - samples]
    else:
        out[:samples] = x[-samples:]
    return out.tobytes()


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize("offset", [6, 667, -1164, 0])
def test_detect_drive_offset_from_disc(offset: int) -> None:
    audio, toc = noise_disc(LENGTHS)
    source = drive(audio, toc, offset=offset)
    matches = detect_offset_from_disc(
        source, toc, responses_for(audio, toc), DriveProfile(caches_audio=False)
    )
    assert detected_drive_offset(matches) == offset
    assert matches[0].tracks == tuple(offset_test_tracks(toc))


def test_disc_not_matching_database_gives_no_offset() -> None:
    audio, toc = noise_disc(LENGTHS)
    other, _ = noise_disc(LENGTHS, seed=99)
    matches = detect_offset_from_disc(
        drive(audio, toc), toc, responses_for(other, toc), DriveProfile()
    )
    assert detected_drive_offset(matches) is None


def test_disc_with_several_pressings_is_ambiguous() -> None:
    """Database knows two pressings (one 652 samples off): both offsets fit, so this
    disc alone must not decide the drive offset."""
    audio, toc = noise_disc(LENGTHS)
    responses = responses_for(audio, toc) + responses_for(shifted(audio, -646), toc)
    matches = detect_offset_from_disc(drive(audio, toc, offset=6), toc, responses, DriveProfile())
    assert offset_candidates(matches) == [6, 652]
    assert detected_drive_offset(matches) is None


def test_offset_test_tracks_avoid_disc_edges() -> None:
    _, toc = noise_disc(LENGTHS)
    assert offset_test_tracks(toc) == [2, 3, 4]  # first and last tracks excluded


# ---------------------------------------------------------------- verification


def rip_tracks(audio: bytes, toc: Toc, **kw: object) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    Extractor(
        drive(audio, toc, **kw), DriveProfile(caches_audio=False), on_track=out.__setitem__
    ).run()
    return out


def test_verify_accurate_rip() -> None:
    audio, toc = noise_disc(LENGTHS)
    report = verify_rip(toc, rip_tracks(audio, toc), responses_for(audio, toc))
    assert report.accurate
    assert report.summary() == "All 5 tracks accurately ripped"
    v = report.tracks[3]
    assert v.v1_confidence == 9 and v.v2_confidence == 7
    assert v.describe() == "Accurately ripped (confidence 9) [v1+v2]"


def test_verify_not_in_database() -> None:
    audio, toc = noise_disc(LENGTHS)
    report = verify_rip(toc, rip_tracks(audio, toc), None)
    assert not report.accurate and not report.in_database
    assert report.summary() == "Disc not present in the AccurateRip database"


def test_verify_damaged_track_is_not_accurate() -> None:
    audio, toc = noise_disc(LENGTHS)
    tracks = rip_tracks(audio, toc)
    t3 = bytearray(tracks[3])
    t3[1000] ^= 0xFF
    tracks[3] = bytes(t3)
    report = verify_rip(toc, tracks, responses_for(audio, toc))
    assert not report.accurate
    assert [n for n, v in report.tracks.items() if not v.accurate] == [3]
    assert report.tracks[3].describe() == "Not accurate (database confidence 9)"


def test_other_pressing_detected_by_offset() -> None:
    audio, toc = noise_disc(LENGTHS)
    # The database only knows a pressing whose music sits 120 samples later. Offsets
    # use the drive-offset convention (how far to move the read window), so this
    # pressing matches when reading 120 samples earlier: -120.
    pressing = shifted(audio, 120)
    report = verify_rip(toc, rip_tracks(audio, toc), responses_for(pressing, toc))
    assert not report.accurate
    assert report.pressings and report.pressings[0].offset == -120
    assert set(report.pressings[0].tracks) >= {2, 3, 4}
    assert "offset -120" in report.summary()
