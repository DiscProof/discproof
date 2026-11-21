"""Formatted Q sub-channel (READ CD sub-channel selection 010b) decoding.

The Q channel tells us, per sector, which track and index the laser is in and the
relative/absolute time. Phase 7 gap detection searches for INDEX 00 -> 01 transitions.
Byte 0 is CONTROL (high nibble) | ADR (low nibble) — the reverse of the TOC descriptor
layout.
"""

from __future__ import annotations

from dataclasses import dataclass

from perfect_audio_copy.drive.constants import FRAMES_PER_SECOND, SUBQ_SIZE
from perfect_audio_copy.drive.crc import cd_crc16
from perfect_audio_copy.drive.msf import bcd_to_int, msf_to_lba

LEADOUT_TRACK = 0xAA


@dataclass(frozen=True, slots=True)
class QPosition:
    """ADR=1 Q frame: current position."""

    control: int
    track: int  # 0xAA for lead-out
    index: int
    relative_frames: int  # counts down towards 0 inside a pre-gap (INDEX 00)
    absolute_lba: int
    crc_ok: bool | None  # None: drive did not supply a CRC


@dataclass(frozen=True, slots=True)
class QOther:
    """Q frame with ADR 2 (MCN) or 3 (ISRC) — carries no position."""

    control: int
    adr: int
    mcn: str | None
    crc_ok: bool | None


def _crc_status(q: bytes) -> bool | None:
    stored = int.from_bytes(q[10:12], "big")
    if stored == 0:
        return None
    return cd_crc16(q[:10]) == stored


def parse_q(q: bytes) -> QPosition | QOther | None:
    """Decode one 16-byte formatted Q record. None if undecodable (bad BCD, unknown ADR)."""
    if len(q) < 12:
        raise ValueError(f"Q sub-channel record must be at least 12 bytes, got {len(q)}")
    control, adr = q[0] >> 4, q[0] & 0x0F
    crc_ok = _crc_status(q)
    try:
        if adr == 1:
            track = q[1] if q[1] == LEADOUT_TRACK else bcd_to_int(q[1])
            index = bcd_to_int(q[2])
            rel = (bcd_to_int(q[3]) * 60 + bcd_to_int(q[4])) * FRAMES_PER_SECOND + bcd_to_int(q[5])
            abs_lba = msf_to_lba(bcd_to_int(q[7]), bcd_to_int(q[8]), bcd_to_int(q[9]))
            return QPosition(control, track, index, rel, abs_lba, crc_ok)
        if adr == 2:
            digits = "".join(f"{b >> 4}{b & 0x0F}" for b in q[1:8])[:13]
            return QOther(control, adr, digits if digits.isdigit() else None, crc_ok)
        if adr == 3:
            return QOther(control, adr, None, crc_ok)
    except ValueError:
        return None
    return None


def split_q_records(blob: bytes) -> list[bytes]:
    return [blob[i : i + SUBQ_SIZE] for i in range(0, len(blob) - SUBQ_SIZE + 1, SUBQ_SIZE)]
