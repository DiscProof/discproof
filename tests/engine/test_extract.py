"""Extraction engine against VirtualDrive: the BUILD_PROMPT integration requirements."""

from __future__ import annotations

import zlib

import pytest
from discs import drive, expected_tracks, noise_disc

from discproof.drive.constants import SECTOR_SIZE
from discproof.drive.toc import Toc
from discproof.engine.events import (
    BlockDone,
    Event,
    Progress,
    Retrying,
    RipResult,
    SectorStatus,
    SpeedChanged,
    SuspiciousPosition,
    TrackDone,
)
from discproof.engine.extract import Extractor, detect_htoa
from discproof.engine.profile import (
    CacheDefeat,
    DriveProfile,
    ExtractionSettings,
    Mode,
    Quality,
)
from discproof.engine.virtual import C2Mode, VirtualDrive

NO_CACHE = DriveProfile(caches_audio=False)


def rip(
    source: VirtualDrive,
    profile: DriveProfile = NO_CACHE,
    settings: ExtractionSettings | None = None,
    toc: Toc | None = None,
) -> tuple[RipResult, dict[int, bytes], list[Event]]:
    tracks: dict[int, bytes] = {}
    events: list[Event] = []

    def on_track(n: int, pcm: bytes) -> None:
        tracks[n] = pcm

    result = Extractor(
        source, profile, settings, toc=toc, on_event=events.append, on_track=on_track
    ).run()
    return result, tracks, events


def assert_identical(tracks: dict[int, bytes], expected: dict[int, bytes]) -> None:
    assert sorted(tracks) == sorted(expected)
    for n, pcm in expected.items():
        assert len(tracks[n]) == len(pcm), f"track {n} length"
        assert tracks[n] == pcm, f"track {n} differs"


# ---------------------------------------------------------------- secure


def test_secure_clean_disc_is_bit_identical() -> None:
    audio, toc = noise_disc()
    result, tracks, events = rip(drive(audio, toc))
    assert_identical(tracks, expected_tracks(audio, toc))
    assert result.ok
    for t in result.tracks:
        assert t.crc32 == zlib.crc32(tracks[t.number])
        assert t.peak > 0
        assert t.sectors == toc.length(t.number)
    progress = [e for e in events if isinstance(e, Progress)]
    assert progress[-1].done == progress[-1].total == toc.leadout
    assert [e.result.number for e in events if isinstance(e, TrackDone)] == [1, 2, 3]


@pytest.mark.parametrize("offset", [667, -1164, 6])
@pytest.mark.parametrize("overread", [False, True])
def test_secure_with_errors_cache_and_offset_is_bit_identical(offset: int, overread: bool) -> None:
    audio, toc = noise_disc()
    source = drive(
        audio,
        toc,
        offset=offset,
        overread_leadin=overread,
        overread_leadout=overread,
        cache_sectors=120,
        fua_flushes=False,  # like the HL-DT-ST GP75N
        error_rate=0.01,
        weak={40: 0.3, 41: 0.3, 500: 0.4},
        seed=11,
    )
    profile = DriveProfile(
        read_offset=offset,
        overread_leadin=overread,
        overread_leadout=overread,
        caches_audio=True,
        cache_defeat=CacheDefeat.SEEK_AWAY,
        cache_sectors=120,
    )
    result, tracks, _ = rip(source, profile)
    expected = expected_tracks(
        audio, toc, offset=offset, overread_leadin=overread, overread_leadout=overread
    )
    assert_identical(tracks, expected)
    assert result.ok
    assert sum(t.recovered_sectors for t in result.tracks) > 0
    missing = sum(t.filled_samples for t in result.tracks)
    assert (missing == 0) if overread else (missing == abs(offset))


def test_cache_without_defeat_misses_errors() -> None:
    """Why cache defeat matters: with the cache answering the second read, a
    transient error is read twice identically and accepted."""
    audio, toc = noise_disc()

    def source() -> VirtualDrive:
        return drive(audio, toc, cache_sectors=300, fua_flushes=False, error_rate=0.05, seed=5)

    _, naive, _ = rip(source(), DriveProfile(caches_audio=False))
    assert naive != expected_tracks(audio, toc)
    _, fixed, _ = rip(source(), DriveProfile(caches_audio=True, cache_sectors=300))
    assert_identical(fixed, expected_tracks(audio, toc))


def test_fua_cache_defeat() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, cache_sectors=200, fua_flushes=True, error_rate=0.02, seed=8)
    profile = DriveProfile(caches_audio=True, cache_defeat=CacheDefeat.FUA)
    _, tracks, _ = rip(source, profile)
    assert_identical(tracks, expected_tracks(audio, toc))
    assert source.stats.flushes > 0


def test_unrecoverable_sector_reported_at_track_timestamp() -> None:
    audio, toc = noise_disc()
    bad = toc.track(2).start + 2 * 75 + 10  # 0:02.10 into track 2
    unreadable = toc.track(3).start + 75 + 3  # 0:01.03 into track 3
    source = drive(audio, toc, bad={bad}, unreadable={unreadable}, seed=4)
    result, tracks, events = rip(source, settings=ExtractionSettings(quality=Quality.LOW))
    t1, t2, t3 = result.tracks
    assert t1.ok
    assert t2.suspicious == ("0:02.10",)
    assert t3.suspicious == ("0:01.03",)
    assert not result.ok
    sus = [e for e in events if isinstance(e, SuspiciousPosition)]
    assert {(e.track, e.lba, e.timestamp) for e in sus} == {
        (2, bad, "0:02.10"),
        (3, unreadable, "0:01.03"),
    }
    # Everything else is still exact; the unreadable sector is silence.
    expected = expected_tracks(audio, toc)
    rel = (unreadable - toc.track(3).start) * SECTOR_SIZE
    assert tracks[3][rel : rel + SECTOR_SIZE] == bytes(SECTOR_SIZE)
    assert tracks[3][:rel] == expected[3][:rel]
    assert tracks[3][rel + SECTOR_SIZE :] == expected[3][rel + SECTOR_SIZE :]
    statuses = [s for e in events if isinstance(e, BlockDone) for s in e.statuses]
    assert statuses.count(SectorStatus.SUSPICIOUS) == 2


@pytest.mark.parametrize("quality", list(Quality))
def test_recovery_rounds_follow_quality(quality: Quality) -> None:
    audio, toc = noise_disc([60])
    source = drive(audio, toc, bad={30})
    settings = ExtractionSettings(quality=quality, recovery_speed=None)
    before = source.stats.commands
    result, _, events = rip(source, settings=settings)
    rounds = [e for e in events if isinstance(e, Retrying)]
    assert [e.round for e in rounds] == list(range(1, quality.value + 1))
    assert result.tracks[0].suspicious
    # 2 reads per batch (3 batches for 60 sectors) + 16 per round for the bad sector
    reads = result.reads
    assert reads == 2 * 3 + 16 * quality.value
    assert source.stats.commands - before >= reads


def test_worst_case_is_82_reads_per_sector() -> None:
    audio, toc = noise_disc([26])
    source = drive(audio, toc, bad={13})
    result, _, _ = rip(
        source, settings=ExtractionSettings(quality=Quality.HIGH, recovery_speed=None)
    )
    assert result.reads == 82


def test_weak_sector_recovered_by_majority() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, weak={100: 0.35}, seed=21)
    result, tracks, _ = rip(source)
    assert_identical(tracks, expected_tracks(audio, toc))
    assert result.ok


def test_speed_drops_for_error_region_then_restores() -> None:
    audio, toc = noise_disc([60])
    source = drive(audio, toc, bad={30})
    settings = ExtractionSettings(quality=Quality.MEDIUM, recovery_speed=4.0)
    _, _, events = rip(source, settings=settings)
    changes = [e.speed for e in events if isinstance(e, SpeedChanged)]
    assert changes == [4.0, None]
    assert source.speed is None


def test_lower_speed_lets_marginal_sector_recover() -> None:
    audio, toc = noise_disc([60])
    # Always wrong at full speed, fine at 4x/24x scaled probability 0.9*4/24 = 0.15.
    source = drive(audio, toc, weak={30: 0.9}, max_speed=24, seed=3)
    settings = ExtractionSettings(quality=Quality.HIGH, recovery_speed=4.0)
    result, tracks, _ = rip(source, settings=settings)
    assert_identical(tracks, expected_tracks(audio, toc))
    assert result.ok


def test_lying_c2_false_flags_do_not_corrupt_output() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, c2=C2Mode.LYING, c2_false_rate=0.05, error_rate=0.01, seed=9)
    profile = DriveProfile(caches_audio=False, c2_supported=True, c2_trusted=True)
    result, tracks, _ = rip(source, profile)
    assert_identical(tracks, expected_tracks(audio, toc))
    assert result.ok


def test_fast_path_reads_once_and_rereads_c2_flagged() -> None:
    audio, toc = noise_disc()
    fast = DriveProfile(caches_audio=False, c2_supported=True, c2_trusted=True)
    assert fast.fast_path
    source = drive(audio, toc, weak={10: 0.3, 600: 0.3}, seed=12)
    result, tracks, _ = rip(source, fast)
    assert_identical(tracks, expected_tracks(audio, toc))
    slow_result, _, _ = rip(drive(audio, toc), NO_CACHE)
    assert result.reads < slow_result.reads


# ---------------------------------------------------------------- jitter


def test_jitter_is_corrected_on_non_accurate_stream_drive() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, jitter=0.3, max_jitter=400, weak={200: 0.3}, seed=17)
    profile = DriveProfile(caches_audio=False, accurate_stream=False, max_jitter=588)
    result, tracks, _ = rip(source, profile)
    assert_identical(tracks, expected_tracks(audio, toc))
    assert result.ok


def test_jitter_without_alignment_breaks_a_burst_rip() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, jitter=0.3, max_jitter=400, seed=17)
    profile = DriveProfile(caches_audio=False, accurate_stream=True)
    _, tracks, _ = rip(source, profile, ExtractionSettings(mode=Mode.BURST))
    assert tracks != expected_tracks(audio, toc)


@pytest.mark.parametrize("seed", range(8))
def test_jitter_aligned_burst_flags_every_wrong_sector(seed: int) -> None:
    """Burst mode has no re-reads, but with alignment every sector it gets wrong
    (only possible where a jittered read overhangs the disc edge) is flagged."""
    audio, toc = noise_disc()
    source = drive(audio, toc, jitter=0.3, max_jitter=400, seed=seed)
    profile = DriveProfile(caches_audio=False, accurate_stream=False)
    events: list[Event] = []
    out: dict[int, bytes] = {}
    Extractor(
        source,
        profile,
        ExtractionSettings(mode=Mode.BURST),
        on_event=events.append,
        on_track=out.__setitem__,
    ).run()
    got = b"".join(out[n] for n in (1, 2, 3))
    want = b"".join(expected_tracks(audio, toc)[n] for n in (1, 2, 3))
    wrong = {
        i
        for i in range(toc.leadout)
        if got[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]
        != want[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]
    }
    flagged = {
        e.lba + i
        for e in events
        if isinstance(e, BlockDone)
        for i, st in enumerate(e.statuses)
        if st is SectorStatus.SUSPICIOUS
    }
    assert wrong <= flagged
    assert wrong <= {0, toc.leadout - 1}


@pytest.mark.parametrize("seed", range(6))
def test_secure_jitter_cache_offset_combined(seed: int) -> None:
    audio, toc = noise_disc()
    source = drive(
        audio,
        toc,
        jitter=0.2,
        max_jitter=300,
        cache_sectors=120,
        fua_flushes=False,
        offset=-30,
        error_rate=0.005,
        seed=seed,
    )
    profile = DriveProfile(
        read_offset=-30, caches_audio=True, cache_sectors=120, accurate_stream=False
    )
    result, tracks, _ = rip(source, profile)
    assert_identical(tracks, expected_tracks(audio, toc, offset=-30))
    assert result.ok


# ---------------------------------------------------------------- burst / test & copy


def test_burst_with_errors_differs_and_is_flagged() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, weak={50: 1.0}, unreadable={400}, seed=2)
    profile = DriveProfile(caches_audio=False, c2_supported=True, c2_trusted=True)
    result, tracks, _ = rip(source, profile, ExtractionSettings(mode=Mode.BURST))
    assert tracks != expected_tracks(audio, toc)
    assert result.tracks[0].suspicious == ("0:00.50",)
    assert result.tracks[1].suspicious == (f"0:01.{400 - 300 - 75:02d}",)
    # One read per batch, except the batch holding the unreadable sector: that one
    # fails and is re-read sector by sector (25 succeed).
    batches = -(-toc.leadout // 26)
    assert result.reads == (batches - 1) + 25


def test_burst_clean_disc_matches_secure() -> None:
    audio, toc = noise_disc()
    _, burst, _ = rip(drive(audio, toc), settings=ExtractionSettings(mode=Mode.BURST))
    assert_identical(burst, expected_tracks(audio, toc))


def test_test_and_copy_matching_crcs() -> None:
    audio, toc = noise_disc()
    result, tracks, events = rip(drive(audio, toc), settings=ExtractionSettings(test_and_copy=True))
    assert_identical(tracks, expected_tracks(audio, toc))
    for t in result.tracks:
        assert t.test_crc32 == t.crc32
        assert not t.crc_mismatch
    passes = [e.pass_name for e in events if isinstance(e, TrackDone)]
    assert passes == ["test"] * 3 + ["copy"] * 3


def test_test_and_copy_flags_crc_mismatch() -> None:
    audio, toc = noise_disc()
    source = drive(audio, toc, bad={350}, seed=6)
    result, _, _ = rip(source, settings=ExtractionSettings(test_and_copy=True, quality=Quality.LOW))
    t2 = result.tracks[1]
    assert t2.crc_mismatch
    assert not t2.ok
    assert result.tracks[0].test_crc32 == result.tracks[0].crc32


# ---------------------------------------------------------------- tracks, HTOA, control


def test_track_selection_rips_only_chosen_tracks() -> None:
    audio, toc = noise_disc()
    result, tracks, _ = rip(drive(audio, toc), settings=ExtractionSettings(tracks=(1, 3)))
    expected = expected_tracks(audio, toc)
    assert_identical(tracks, {1: expected[1], 3: expected[3]})
    assert [t.number for t in result.tracks] == [1, 3]


def test_htoa_detected_and_ripped_as_track_zero() -> None:
    audio, toc = noise_disc(first_start=40)
    source = drive(audio, toc)
    info = detect_htoa(source, NO_CACHE)
    assert info is not None
    assert info.sectors == 40
    assert not info.silent
    _, tracks, _ = rip(source, settings=ExtractionSettings(rip_htoa=True))
    assert_identical(tracks, expected_tracks(audio, toc, htoa=True))


def test_no_htoa_when_track_one_starts_at_zero() -> None:
    audio, toc = noise_disc()
    assert detect_htoa(drive(audio, toc), NO_CACHE) is None


def test_silent_htoa() -> None:
    audio, toc = noise_disc(first_start=30)
    audio = bytes(30 * SECTOR_SIZE) + audio[30 * SECTOR_SIZE :]
    info = detect_htoa(drive(audio, toc), NO_CACHE)
    assert info is not None and info.silent


def test_cancel_stops_the_rip() -> None:
    audio, toc = noise_disc()
    extractor: Extractor

    def on_event(e: Event) -> None:
        if isinstance(e, Progress) and e.done >= 100:
            extractor.cancel()

    extractor = Extractor(drive(audio, toc), NO_CACHE, on_event=on_event)
    result = extractor.run()
    assert result.cancelled
    assert not result.ok
    assert all(t.number == 1 for t in result.tracks) or not result.tracks


def test_data_track_is_never_read() -> None:
    from discproof.engine.virtual import make_toc

    audio, _ = noise_disc()
    toc = make_toc([300, 250, 400], data_track=3)
    source = drive(audio, toc, offset=600)
    result, tracks, _ = rip(source, DriveProfile(caches_audio=False, read_offset=600))
    assert [t.number for t in result.tracks] == [1, 2]
    # The offset pushes the end of track 2 into the data track: filled with silence.
    assert result.tracks[1].filled_samples == 600
    assert tracks[2][-600 * 4 :] == bytes(600 * 4)
    assert tracks[2][: -600 * 4] == audio[300 * SECTOR_SIZE : 550 * SECTOR_SIZE - 600 * 4]
