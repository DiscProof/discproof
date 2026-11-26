"""CD-DA PCM helpers: sample views, CRC32 and peak level.

Audio is 16-bit signed little-endian stereo; one "sample" is one L+R pair (4 bytes),
588 per sector.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from discproof.drive.constants import BYTES_PER_SAMPLE, SAMPLES_PER_SECTOR

SampleArray = npt.NDArray[np.uint32]


def as_samples(pcm: bytes | bytearray | memoryview) -> SampleArray:
    """View PCM bytes as one uint32 per stereo sample (for fast comparison)."""
    if len(pcm) % BYTES_PER_SAMPLE:
        raise ValueError(f"PCM length {len(pcm)} is not a whole number of samples")
    return np.frombuffer(pcm, dtype="<u4")


def silence(samples: int) -> bytes:
    return bytes(samples * BYTES_PER_SAMPLE)


def peak(pcm: bytes) -> int:
    """Largest absolute 16-bit sample value (0..32768)."""
    if not pcm:
        return 0
    values = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    return int(np.abs(values).max())


@dataclass(slots=True)
class TrackChecksum:
    """Incremental CRC32 (EAC "Copy CRC": CRC32 of the raw PCM) and peak level."""

    crc32: int = 0
    peak: int = 0
    samples: int = 0

    def update(self, pcm: bytes) -> None:
        self.crc32 = zlib.crc32(pcm, self.crc32)
        self.peak = max(self.peak, peak(pcm))
        self.samples += len(pcm) // BYTES_PER_SAMPLE

    @property
    def peak_percent(self) -> float:
        return self.peak * 100.0 / 32768.0

    @property
    def sectors(self) -> float:
        return self.samples / SAMPLES_PER_SECTOR
