"""Drive feature detection against VirtualDrive (simulated clock)."""

from __future__ import annotations

import pytest
from discs import drive, noise_disc

from perfect_audio_copy.drive.constants import C2_SIZE, SECTOR_SIZE
from perfect_audio_copy.engine.profile import CacheDefeat, DriveProfile
from perfect_audio_copy.engine.virtual import C2Mode, VirtualDrive
from perfect_audio_copy.verify.features import FeatureDetector, _judge_c2


def detector(d: VirtualDrive) -> FeatureDetector:
    return FeatureDetector(d, d.toc, max_speed_x=d.max_speed, clock=d.clock)


def test_non_caching_accurate_drive() -> None:
    audio, toc = noise_disc()
    report = detector(drive(audio, toc)).run()
    assert not report.cache.caches_audio and report.cache.consistent
    assert report.accurate_stream
    assert report.c2.supported and not report.c2.tested and not report.c2.trusted
    assert not report.overread_leadin and not report.overread_leadout
    assert any("scratched" in n for n in report.notes)


def test_caching_drive_that_ignores_fua() -> None:
    """The HL-DT-ST GP75N case."""
    audio, toc = noise_disc()
    report = detector(drive(audio, toc, cache_sectors=300, fua_flushes=False)).run()
    cache = report.cache
    assert cache.caches_audio and cache.consistent
    assert cache.fua_works is False
    assert cache.seek_away_works is True
    assert cache.cache_sectors is not None and cache.cache_sectors >= 300
    profile = report.apply(DriveProfile())
    assert profile.caches_audio and profile.cache_defeat is CacheDefeat.SEEK_AWAY
    assert profile.cache_sectors == cache.cache_sectors


def test_caching_drive_with_working_fua() -> None:
    audio, toc = noise_disc()
    report = detector(drive(audio, toc, cache_sectors=200, fua_flushes=True)).run()
    assert report.cache.fua_works is True
    assert report.apply(DriveProfile()).cache_defeat is CacheDefeat.FUA


def test_jittery_drive_has_no_accurate_stream() -> None:
    audio, toc = noise_disc()
    report = detector(drive(audio, toc, jitter=1.0, max_jitter=300, seed=4)).run()
    assert not report.accurate_stream
    assert not report.apply(DriveProfile()).accurate_stream


def test_c2_unsupported() -> None:
    audio, toc = noise_disc()
    report = detector(drive(audio, toc, c2=C2Mode.NONE)).run()
    assert not report.c2.supported
    assert not report.apply(DriveProfile(c2_supported=True)).c2_supported


def scratched(**kw: object) -> VirtualDrive:
    audio, toc = noise_disc()
    weak = {lba: 0.5 for lba in range(230, 260)} | {lba: 0.5 for lba in range(470, 500)}
    return drive(audio, toc, weak=weak, seed=13, **kw)


def test_accurate_c2_is_trusted_on_a_scratched_disc() -> None:
    d = scratched(c2=C2Mode.ACCURATE)
    c2 = detector(d).detect_c2([230, 470])
    assert c2.tested and c2.missed == 0 and c2.trusted


def test_lying_c2_is_not_trusted() -> None:
    d = scratched(c2=C2Mode.LYING, c2_miss_rate=0.5)
    c2 = detector(d).detect_c2([230, 470])
    assert c2.tested and c2.missed > 0 and not c2.trusted


def concealing(**kw: object) -> VirtualDrive:
    """A scratched disc on a drive whose error concealment is repeatable (the GP75N):
    most reads of a weak sector come back wrong, and wrong the same way each time."""
    audio, toc = noise_disc()
    weak = {lba: 0.7 for lba in range(230, 256)} | {lba: 0.7 for lba in range(470, 496)}
    return drive(audio, toc, weak=weak, repeatable_errors=True, seed=13, **kw)


def test_concealed_majority_does_not_outvote_clean_reads() -> None:
    """Identical flagged reads can outnumber the clean ones; they must not become the
    reference, or the clean reads would count as missed errors."""
    c2 = detector(concealing(c2=C2Mode.ACCURATE)).detect_c2([230, 470])
    assert c2.tested and c2.missed == 0 and c2.false_flags == 0 and c2.trusted


def test_lying_c2_with_concealment_is_not_trusted() -> None:
    c2 = detector(concealing(c2=C2Mode.LYING, c2_miss_rate=0.5)).detect_c2([230, 470])
    assert c2.tested and c2.missed > 0 and not c2.trusted


def test_sector_never_read_clean_proves_nothing() -> None:
    """Every read flagged: there is no clean reference, so nothing is counted."""
    audio, toc = noise_disc()
    d = drive(audio, toc, bad=set(range(230, 256)), repeatable_errors=True, seed=13)
    c2 = detector(d).detect_c2([230])
    assert c2.sectors_tested == 0 and not c2.tested and not c2.trusted


GOOD = bytes(range(256)) * (SECTOR_SIZE // 256) + bytes(SECTOR_SIZE % 256)
NO_C2 = bytes(C2_SIZE)


def damaged(*positions: int) -> bytes:
    out = bytearray(GOOD)
    for p in positions:
        out[p] ^= 0xFF
    return bytes(out)


def pointers(*positions: int) -> bytes:
    bits = bytearray(C2_SIZE)
    for p in positions:
        bits[p >> 3] |= 0x80 >> (p & 7)
    return bytes(bits)


def test_c2_covering_every_bad_byte_is_a_catch() -> None:
    reads = [GOOD, damaged(10, 11), GOOD]
    c2 = [NO_C2, pointers(8, 9, 10, 11), NO_C2]
    assert _judge_c2(reads, c2) == (1, 0, 0)


def test_c2_on_the_wrong_bytes_is_a_miss() -> None:
    """Flagged sector, but one bad byte has no C2 bit: C2 cannot locate the error."""
    reads = [GOOD, damaged(10, 500), GOOD]
    c2 = [NO_C2, pointers(10), NO_C2]
    assert _judge_c2(reads, c2) == (1, 1, 0)


def test_lone_wrong_clean_read_is_exposed_by_flagged_reads() -> None:
    """The only unflagged read is wrong at byte 700. The flagged reads are wrong only
    where they say, so they differ from it at byte 700 without a flag there."""
    reads = [damaged(700), damaged(10), damaged(20)]
    c2 = [NO_C2, pointers(10), pointers(20)]
    errors, missed, _ = _judge_c2(reads, c2)
    assert errors == 2 and missed == 2


def test_flagged_read_with_correct_data_is_a_false_flag() -> None:
    assert _judge_c2([GOOD, GOOD], [NO_C2, pointers(5)]) == (0, 0, 1)


def test_overread_detected() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, overread_leadin=True, overread_leadout=True)
    report = detector(d).run()
    assert report.overread_leadin and report.overread_leadout


@pytest.mark.parametrize("cache", [0, 300])
def test_detected_profile_rips_correctly(cache: int) -> None:
    """End to end: detect features, then a secure rip with the result is exact."""
    from discs import expected_tracks

    from perfect_audio_copy.engine.extract import Extractor

    audio, toc = noise_disc()

    def make() -> VirtualDrive:
        return drive(audio, toc, cache_sectors=cache, fua_flushes=False, error_rate=0.02, seed=1)

    profile = detector(make()).run().apply(DriveProfile())
    out: dict[int, bytes] = {}
    Extractor(make(), profile, on_track=out.__setitem__).run()
    assert out == expected_tracks(audio, toc)
