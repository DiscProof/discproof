"""Real-drive checks. Skipped unless PAC_TEST_DRIVE names a device with an audio CD in it:

PAC_TEST_DRIVE=/dev/sr0 .venv/bin/pytest -m hardware -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from perfect_audio_copy.drive.constants import SECTOR_SIZE
from perfect_audio_copy.drive.device import Drive

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
