"""CRC-16/CCITT as used by CD-Text packs and the Q sub-channel."""

from __future__ import annotations


def _make_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        table.append(crc & 0xFFFF)
    return tuple(table)


_TABLE = _make_table()


def crc16_ccitt(data: bytes) -> int:
    """CRC-16, polynomial 0x1021, initial value 0, no reflection (a.k.a. XMODEM)."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[(crc >> 8) ^ byte]
    return crc


def cd_crc16(data: bytes) -> int:
    """The CRC stored on CD (CD-Text, Q sub-channel): CRC-16/CCITT, bitwise inverted."""
    return crc16_ccitt(data) ^ 0xFFFF
