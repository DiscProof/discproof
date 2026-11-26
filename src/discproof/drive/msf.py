"""Conversions between LBA, MSF (minutes:seconds:frames) and BCD."""

from __future__ import annotations

from discproof.drive.constants import FRAMES_PER_SECOND, MSF_OFFSET


def msf_to_lba(m: int, s: int, f: int) -> int:
    """Absolute MSF (as stored in the TOC) to LBA. 00:02:00 -> 0."""
    return (m * 60 + s) * FRAMES_PER_SECOND + f - MSF_OFFSET


def lba_to_msf(lba: int) -> tuple[int, int, int]:
    """LBA to absolute MSF. 0 -> (0, 2, 0)."""
    total = lba + MSF_OFFSET
    if total < 0:
        raise ValueError(f"LBA {lba} is before the start of the disc")
    m, rest = divmod(total, 60 * FRAMES_PER_SECOND)
    s, f = divmod(rest, FRAMES_PER_SECOND)
    return m, s, f


def frames_to_timestamp(frames: int) -> str:
    """Duration/relative position in sectors to ``mm:ss.ff`` (EAC log style)."""
    sign = "-" if frames < 0 else ""
    m, rest = divmod(abs(frames), 60 * FRAMES_PER_SECOND)
    s, f = divmod(rest, FRAMES_PER_SECOND)
    return f"{sign}{m}:{s:02d}.{f:02d}"


def bcd_to_int(value: int) -> int:
    hi, lo = value >> 4, value & 0x0F
    if hi > 9 or lo > 9:
        raise ValueError(f"0x{value:02x} is not valid BCD")
    return hi * 10 + lo


def int_to_bcd(value: int) -> int:
    if not 0 <= value <= 99:
        raise ValueError(f"{value} cannot be encoded as one BCD byte")
    return (value // 10) << 4 | value % 10
