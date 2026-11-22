"""Synthetic discs for engine tests."""

from __future__ import annotations

import numpy as np

from perfect_audio_copy.drive.constants import SAMPLES_PER_SECTOR, SECTOR_SIZE
from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.engine.virtual import VirtualDrive, make_toc

LENGTHS = [300, 250, 400]


def noise_disc(
    lengths: list[int] = LENGTHS, *, first_start: int = 0, seed: int = 1
) -> tuple[bytes, Toc]:
    """Audio that is noise everywhere (so every sample position is distinctive),
    with a short digitally silent stretch inside track 2."""
    toc = make_toc(lengths, first_start=first_start)
    total = toc.leadout * SAMPLES_PER_SECTOR * 2
    rng = np.random.default_rng(seed)
    pcm = rng.integers(-12000, 12000, size=total, dtype=np.int16)
    if len(lengths) > 1:
        t2 = toc.track(2).start
        pcm[(t2 + 50) * SAMPLES_PER_SECTOR * 2 : (t2 + 60) * SAMPLES_PER_SECTOR * 2] = 0
    return pcm.astype("<i2").tobytes(), toc


def expected_tracks(
    audio: bytes,
    toc: Toc,
    *,
    offset: int = 0,
    overread_leadin: bool = False,
    overread_leadout: bool = False,
    htoa: bool = False,
) -> dict[int, bytes]:
    """What a correct rip must produce: the true audio, with silence where the drive
    cannot reach (offset pushes the read before LBA 0 / past the lead-out)."""
    out = bytearray(audio)
    total = len(audio) // 4
    for t in range(total):
        d = (t + offset) // SAMPLES_PER_SECTOR
        lo = -(10**9) if overread_leadin else 0
        hi = 10**9 if overread_leadout else toc.leadout
        if not lo <= d < hi:
            out[t * 4 : t * 4 + 4] = b"\x00\x00\x00\x00"
    tracks = {
        t.number: bytes(out[t.start * SECTOR_SIZE : toc.end(t.number) * SECTOR_SIZE])
        for t in toc.audio_tracks
    }
    if htoa:
        tracks[0] = bytes(out[0 : toc.tracks[0].start * SECTOR_SIZE])
    return tracks


def drive(audio: bytes, toc: Toc, **kwargs: object) -> VirtualDrive:
    return VirtualDrive(audio, toc, **kwargs)  # type: ignore[arg-type]
