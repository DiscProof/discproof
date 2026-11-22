"""Real-drive checks. Skipped unless PAC_TEST_DRIVE names a device with an audio CD in it:

PAC_TEST_DRIVE=/dev/sr0 .venv/bin/pytest -m hardware -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from perfect_audio_copy.drive.constants import SECTOR_SIZE
from perfect_audio_copy.drive.device import Drive
from perfect_audio_copy.drive.subchannel import QPosition, parse_q

pytestmark = pytest.mark.hardware

DEVICE = os.environ.get("PAC_TEST_DRIVE")


@pytest.fixture(scope="module")
def drive() -> Iterator[Drive]:
    if not DEVICE:
        pytest.skip("set PAC_TEST_DRIVE=/dev/srN to run hardware tests")
    with Drive.open(DEVICE) as d:
        if not d.wait_ready(30):
            pytest.skip("no disc in drive")
        yield d


def test_inquiry(drive: Drive) -> None:
    assert drive.inquiry().is_mmc


def test_toc_full_matches_basic(drive: Drive) -> None:
    toc = drive.read_toc()
    assert toc.audio_tracks, "insert an audio CD"
    assert toc.tracks[0].start >= 0


def test_read_is_repeatable(drive: Drive) -> None:
    toc = drive.read_toc()
    start = toc.audio_tracks[0].start + 1000
    a = drive.read_cd(start, 10)
    drive.read_cd(start + 20000, 30)  # move away so a cache is less likely to answer
    b = drive.read_cd(start, 10)
    assert len(a.audio) == 10 * SECTOR_SIZE
    assert a.audio == b.audio


def test_c2_and_q_layout(drive: Drive) -> None:
    """Combined C2+Q reads must yield Q records that decode to the sector being read.
    Catches drives that order the C2 and Q fields differently from what we parse."""
    toc = drive.read_toc()
    start = toc.audio_tracks[0].start + 500
    r = drive.read_cd(start, 10, c2=True, subq=True)
    assert drive.q_before_c2 is not None, "could not tell where the drive puts Q vs C2"
    assert r.subq is not None
    good = 0
    for i, q in enumerate(r.subq):
        pos = parse_q(q)
        if isinstance(pos, QPosition) and pos.crc_ok and pos.absolute_lba == start + i:
            good += 1
    assert good >= 8, f"only {good}/10 Q records decoded to their own LBA"


def test_secure_rip_is_repeatable(drive: Drive) -> None:
    """Secure-rip the shortest audio track twice; the CRCs must match. Uses the
    conservative default profile (cache assumed, defeated by seeking away)."""
    from perfect_audio_copy.engine import DriveProfile, ExtractionSettings, Extractor, Quality

    toc = drive.read_toc()
    shortest = min(toc.audio_tracks, key=lambda t: toc.length(t.number))
    settings = ExtractionSettings(tracks=(shortest.number,), quality=Quality.LOW)
    first = Extractor(drive, DriveProfile(), settings, toc=toc).run()
    second = Extractor(drive, DriveProfile(), settings, toc=toc).run()
    assert first.tracks[0].crc32 == second.tracks[0].crc32
    assert first.ok, f"suspicious positions: {first.tracks[0].suspicious}"


def test_gap_detection_matches_brute_force(drive: Drive) -> None:
    """Every pre-gap is confirmed, and at three tracks the boundary found by the
    search equals the one found by reading Q at every sector around it."""
    from perfect_audio_copy.engine.gaps import GapDetector

    toc = drive.read_toc()
    found = GapDetector(drive, toc).detect_all()
    assert all(r.confirmed for r in found.values()), found
    checked = 0
    for n, r in found.items():
        if n == toc.first or not r.pregap or checked == 3:
            continue
        start = toc.track(n).start
        a = start - r.pregap - 6
        sub = drive.read_cd(a, 12, subq=True).subq or ()
        tracks = [
            q.track if isinstance(q, QPosition) and q.crc_ok else None for q in map(parse_q, sub)
        ]
        first = next(a + i for i, t in enumerate(tracks) if t == n)
        assert start - first == r.pregap, (n, start - first, r.pregap)
        checked += 1
