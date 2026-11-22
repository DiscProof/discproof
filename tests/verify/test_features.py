"""Drive feature detection against VirtualDrive (simulated clock)."""

from __future__ import annotations

import pytest
from discs import drive, noise_disc

from perfect_audio_copy.engine.profile import CacheDefeat, DriveProfile
from perfect_audio_copy.engine.virtual import C2Mode, VirtualDrive
from perfect_audio_copy.verify.features import FeatureDetector


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
