"""VirtualDrive: the simulator itself must behave like the drives it models."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from discs import drive, noise_disc

from perfect_audio_copy.drive.constants import SAMPLES_PER_SECTOR, SECTOR_SIZE
from perfect_audio_copy.drive.sense import IllegalRequestError, MediumError
from perfect_audio_copy.drive.subchannel import QPosition, parse_q
from perfect_audio_copy.engine.virtual import C2Mode, VirtualDrive, load_wav, make_toc


def test_clean_read_returns_disc_audio() -> None:
    audio, toc = noise_disc()
    r = drive(audio, toc).read_cd(100, 5)
    assert r.audio == audio[100 * SECTOR_SIZE : 105 * SECTOR_SIZE]


def test_offset_returns_data_early() -> None:
    audio, toc = noise_disc()
    r = drive(audio, toc, offset=30).read_cd(100, 1)
    start = (100 * SAMPLES_PER_SECTOR - 30) * 4
    assert r.audio == audio[start : start + SECTOR_SIZE]


def test_out_of_range_without_overread() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc)
    with pytest.raises(IllegalRequestError) as exc:
        d.read_cd(-1, 1)
    assert exc.value.sense is not None and exc.value.sense.asc == 0x21
    with pytest.raises(IllegalRequestError):
        d.read_cd(toc.leadout - 1, 2)


def test_overread_returns_silence_outside_the_disc() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, overread_leadin=True, overread_leadout=True)
    assert d.read_cd(-2, 2).audio == bytes(2 * SECTOR_SIZE)
    assert d.read_cd(toc.leadout, 1).audio == bytes(SECTOR_SIZE)


def test_cache_repeats_data_until_flushed() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, cache_sectors=100, error_rate=0.5, seed=3)
    a = d.read_cd(10, 5).audio
    assert d.read_cd(10, 5).audio == a  # cache hit, same errors
    assert d.stats.disc_reads == 1
    d.flush_cache(10)
    d.read_cd(10, 5)
    assert d.stats.disc_reads == 2


def test_fua_ignored_but_far_read_evicts() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, cache_sectors=100, fua_flushes=False)
    d.read_cd(10, 5)
    d.flush_cache(10)
    d.read_cd(10, 5)
    assert d.stats.disc_reads == 1
    d.read_cd(800, 1)
    d.read_cd(10, 5)
    assert d.stats.disc_reads == 3


def test_unreadable_sector_is_medium_error() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, unreadable={50})
    with pytest.raises(MediumError):
        d.read_cd(48, 4)
    assert d.read_cd(51, 4).audio == audio[51 * SECTOR_SIZE : 55 * SECTOR_SIZE]


def test_accurate_c2_flags_exactly_the_bad_sectors() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, bad={21}, c2=C2Mode.ACCURATE)
    r = d.read_cd(20, 3, c2=True)
    assert r.c2 is not None
    per_sector = [r.c2[i * 294 : (i + 1) * 294] for i in range(3)]
    assert [any(s) for s in per_sector] == [False, True, False]
    assert r.sector(1) != audio[21 * SECTOR_SIZE : 22 * SECTOR_SIZE]
    assert r.sector(0) == audio[20 * SECTOR_SIZE : 21 * SECTOR_SIZE]


def test_c2_unsupported_is_rejected() -> None:
    audio, toc = noise_disc()
    with pytest.raises(IllegalRequestError):
        drive(audio, toc, c2=C2Mode.NONE).read_cd(0, 1, c2=True)


def test_bad_sector_differs_every_read() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, bad={5})
    versions = {d.read_cd(5, 1).audio for _ in range(6)}
    assert len(versions) == 6


def test_jitter_shifts_some_reads() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, jitter=1.0, max_jitter=50, seed=2)
    good = audio[100 * SECTOR_SIZE : 101 * SECTOR_SIZE]
    assert all(d.read_cd(100, 1).audio != good for _ in range(5))


def test_lower_speed_reduces_errors() -> None:
    audio, toc = noise_disc()
    d = drive(audio, toc, weak={7: 1.0}, max_speed=24)
    good = audio[7 * SECTOR_SIZE : 8 * SECTOR_SIZE]
    assert d.read_cd(7, 1).audio != good
    d.set_speed(1.0)
    hits = sum(d.read_cd(7, 1).audio == good for _ in range(50))
    assert hits > 40


def test_q_subchannel_with_pregaps() -> None:
    audio, toc = noise_disc(first_start=20)
    d = drive(audio, toc, pregaps={2: 10})
    t2 = toc.track(2).start
    cases = {0: (1, 0, 20), 25: (1, 1, 5), t2 - 3: (2, 0, 3), t2: (2, 1, 0)}
    for lba, (track, index, rel) in cases.items():
        q = parse_q(d.q_record(lba))
        assert isinstance(q, QPosition)
        assert (q.track, q.index, q.relative_frames, q.absolute_lba) == (track, index, rel, lba)
        assert q.crc_ok
    r = d.read_cd(t2, 1, subq=True)
    assert r.subq is not None


def test_load_wav(tmp_path: Path) -> None:
    audio, _ = noise_disc([3])
    path = tmp_path / "disc.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(audio)
    assert load_wav(path) == audio
    d = VirtualDrive.from_wav(path, make_toc([3]))
    assert d.read_cd(0, 3).audio == audio


def test_c2_bits_match_sector_layout() -> None:
    audio, toc = noise_disc()
    r = drive(audio, toc, bad={3}).read_cd(3, 1, c2=True)
    assert r.c2 is not None
    flagged = np.unpackbits(np.frombuffer(r.c2, dtype=np.uint8)).astype(bool)
    diff = np.frombuffer(r.audio, np.uint8) != np.frombuffer(
        audio[3 * SECTOR_SIZE : 4 * SECTOR_SIZE], np.uint8
    )
    assert not (diff & ~flagged).any()  # every changed byte is flagged
