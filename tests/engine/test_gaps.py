"""Pre-gap detection from the Q sub-channel against VirtualDrive."""

from __future__ import annotations

import pytest
from discs import drive, noise_disc

from discproof.drive.constants import SECTOR_SIZE
from discproof.engine.gaps import TOLERANCE, GapDetector
from discproof.engine.virtual import make_toc

LENGTHS = [900, 700, 800, 600, 1200]
PREGAPS = {2: 150, 3: 0, 4: 37, 5: 500}


def silent_gaps(audio: bytes, toc, pregaps: dict[int, int]) -> bytes:  # type: ignore[no-untyped-def]
    out = bytearray(audio)
    for n, g in pregaps.items():
        s = toc.track(n).start
        out[(s - g) * SECTOR_SIZE : s * SECTOR_SIZE] = bytes(g * SECTOR_SIZE)
    return bytes(out)


@pytest.mark.parametrize("lag", [0, 1, 2, -1, -2])
def test_exact_gaps_with_q_lag(lag: int) -> None:
    audio, toc = noise_disc(LENGTHS)
    d = drive(audio, toc, pregaps=PREGAPS, q_lag=lag)
    found = GapDetector(d, toc, check_silence=False).detect_all()
    assert {n: r.pregap for n, r in found.items() if n > 1} == PREGAPS
    assert all(r.confirmed for r in found.values())


@pytest.mark.parametrize("seed", range(5))
def test_noisy_q_within_tolerance(seed: int) -> None:
    audio, toc = noise_disc(LENGTHS)
    d = drive(audio, toc, pregaps=PREGAPS, q_error_rate=0.1, q_mode23_rate=0.05, q_lag=1, seed=seed)
    found = GapDetector(d, toc, check_silence=False).detect_all()
    for n, want in PREGAPS.items():
        assert abs(found[n].pregap - want) <= TOLERANCE, (n, found[n])


def test_read_count_is_modest() -> None:
    audio, toc = noise_disc(LENGTHS)
    d = drive(audio, toc, pregaps=PREGAPS)
    det = GapDetector(d, toc, check_silence=False)
    det.detect_all()
    assert det.reads <= 15 * len(PREGAPS)


def test_gap_silence_and_htoa() -> None:
    audio, toc = noise_disc(LENGTHS, first_start=60)
    pregaps = {2: 150, 4: 37, 5: 500}
    audio = silent_gaps(audio, toc, {2: 150, 4: 37})  # track 5's gap holds audio
    found = GapDetector(drive(audio, toc, pregaps=pregaps), toc).detect_all()
    assert found[1].pregap == 60 and found[1].silent is False  # hidden track with audio
    assert found[2].silent is True and found[4].silent is True
    assert found[5].silent is False
    assert found[3].pregap == 0 and found[3].silent is None


def test_track_after_data_track_is_skipped() -> None:
    audio, _ = noise_disc(LENGTHS)
    toc = make_toc(LENGTHS, data_track=2)
    found = GapDetector(drive(audio, toc), toc, check_silence=False).detect_all()
    assert 2 not in found and found[3].pregap == 0 and not found[3].confirmed


def test_progress_callback() -> None:
    audio, toc = noise_disc(LENGTHS)
    seen: list[tuple[int, int]] = []
    GapDetector(
        drive(audio, toc), toc, check_silence=False, on_progress=lambda d, t: seen.append((d, t))
    ).detect_all([2, 3])
    assert seen == [(1, 2), (2, 2)]


def test_noise_floor_gap_is_quiet_not_silent() -> None:
    """Like Anodyne's gaps: tape hiss at ~0.1 % of full scale is near-silent."""
    import numpy as np

    audio, toc = noise_disc(LENGTHS)
    pcm = np.frombuffer(audio, "<i2").copy()
    s = toc.track(2).start
    rng = np.random.default_rng(0)
    pcm[(s - 150) * 1176 : s * 1176] = rng.integers(-23, 24, size=150 * 1176)
    found = GapDetector(drive(pcm.tobytes(), toc, pregaps={2: 150}), toc).detect(2)
    assert found.peak is not None and 0 < found.peak < 328
    assert found.silent is False and found.quiet is True
    loud = GapDetector(drive(audio, toc, pregaps={2: 150}), toc).detect(2)
    assert loud.quiet is False
