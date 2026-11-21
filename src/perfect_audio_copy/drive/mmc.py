"""MMC command descriptor blocks (CDBs) and response parsers.

Everything here is pure (bytes in, bytes/dataclasses out) so it can be unit-tested
without hardware. References: SCSI Multimedia Commands (MMC-3/MMC-5), SPC-3.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from enum import IntEnum

from perfect_audio_copy.drive.constants import KBPS_1X, SPEED_MAX


class Op(IntEnum):
    TEST_UNIT_READY = 0x00
    INQUIRY = 0x12
    START_STOP_UNIT = 0x1B
    PREVENT_ALLOW_MEDIUM_REMOVAL = 0x1E
    READ_SUBCHANNEL = 0x42
    READ_TOC = 0x43
    GET_CONFIGURATION = 0x46
    MODE_SENSE_10 = 0x5A
    READ_12 = 0xA8
    SET_CD_SPEED = 0xBB
    READ_CD = 0xBE


class TocFormat(IntEnum):
    TOC = 0x0
    SESSION_INFO = 0x1
    FULL_TOC = 0x2
    CD_TEXT = 0x5


class ErrorField(IntEnum):
    """READ CD byte 9 "error field": what error information to append per sector."""

    NONE = 0b00
    C2 = 0b01  # 294 bytes of C2 error pointers
    C2_AND_BLOCK = 0b10  # 294 bytes + block error byte + pad (296)


class SubChannel(IntEnum):
    """READ CD byte 10 sub-channel data selection."""

    NONE = 0b000
    RAW = 0b001  # 96 bytes raw P-W
    Q = 0b010  # 16 bytes formatted Q
    RW = 0b100  # 96 bytes corrected, de-interleaved R-W


# ---------------------------------------------------------------- CDBs


def _u16(v: int) -> bytes:
    return struct.pack(">H", v)


def _u32(v: int) -> bytes:
    return struct.pack(">I", v & 0xFFFFFFFF)


def cdb_test_unit_ready() -> bytes:
    return bytes(6)


def cdb_inquiry(alloc: int = 96) -> bytes:
    return bytes([Op.INQUIRY, 0, 0, 0, alloc & 0xFF, 0])


def cdb_read_toc(fmt: TocFormat, alloc: int, *, msf: bool = False, track: int = 0) -> bytes:
    """READ TOC/PMA/ATIP. Format 2 (full TOC) and 5 (CD-Text) always return MSF/packs."""
    return (
        bytes([Op.READ_TOC, 0x02 if msf else 0x00, int(fmt) & 0x0F, 0, 0, 0, track & 0xFF])
        + _u16(alloc)
        + b"\x00"
    )


def cdb_read_subchannel(data_format: int, alloc: int, track: int = 0) -> bytes:
    """READ SUB-CHANNEL with SubQ=1. data_format: 1 position, 2 MCN, 3 ISRC."""
    head = bytes([Op.READ_SUBCHANNEL, 0, 0x40, data_format, 0, 0, track & 0xFF])
    return head + _u16(alloc) + b"\x00"


def cdb_read_cd(
    lba: int,
    count: int,
    *,
    error_field: ErrorField = ErrorField.NONE,
    subchannel: SubChannel = SubChannel.NONE,
) -> bytes:
    """READ CD for CD-DA sectors: expected sector type 1 (CD-DA), user data only."""
    if not 0 < count <= 0xFFFFFF:
        raise ValueError(f"invalid sector count {count}")
    sector_type_cdda = 1 << 2
    flags = 0x10 | (int(error_field) << 1)  # user data + error field
    return (
        bytes([Op.READ_CD, sector_type_cdda])
        + _u32(lba)
        + count.to_bytes(3, "big")
        + bytes([flags, int(subchannel), 0])
    )


def cdb_read12_fua(lba: int, count: int = 0) -> bytes:
    """READ(12) with Force Unit Access.

    With a transfer length of 0 this transfers nothing but tells the drive the cached
    copy of ``lba`` must not be used — the cheapest cache-defeat method on drives that
    honour it. The engine verifies per drive whether it actually works.
    """
    return bytes([Op.READ_12, 0x08]) + _u32(lba) + _u32(count) + bytes(2)


def cdb_set_cd_speed(read_kbps: int = SPEED_MAX, write_kbps: int = SPEED_MAX) -> bytes:
    return (
        bytes([Op.SET_CD_SPEED, 0])
        + _u16(min(read_kbps, SPEED_MAX))
        + _u16(min(write_kbps, SPEED_MAX))
        + bytes(6)
    )


def speed_to_kbps(multiplier: float | None) -> int:
    """1x = 176.4 kB/s. ``None`` or 0 means "as fast as possible"."""
    if not multiplier:
        return SPEED_MAX
    return min(SPEED_MAX, round(multiplier * KBPS_1X))


def cdb_mode_sense10(page: int, alloc: int = 256, *, page_control: int = 0) -> bytes:
    """MODE SENSE(10) with DBD=1 (no block descriptors). page_control 0 = current values."""
    return (
        bytes([Op.MODE_SENSE_10, 0x08, (page_control << 6) | (page & 0x3F), 0, 0, 0, 0])
        + _u16(alloc)
        + b"\x00"
    )


def cdb_get_configuration(feature: int, alloc: int = 64) -> bytes:
    """GET CONFIGURATION with RT=10b: only the requested feature descriptor."""
    return bytes([Op.GET_CONFIGURATION, 0x02]) + _u16(feature) + bytes(3) + _u16(alloc) + b"\x00"


def cdb_start_stop(*, load_eject: bool, start: bool, immediate: bool = True) -> bytes:
    return bytes([Op.START_STOP_UNIT, 0x01 if immediate else 0, 0, 0, (load_eject << 1) | start, 0])


def cdb_prevent_allow(prevent: bool) -> bytes:
    return bytes([Op.PREVENT_ALLOW_MEDIUM_REMOVAL, 0, 0, 0, 1 if prevent else 0, 0])


# ---------------------------------------------------------------- parsers


@dataclass(frozen=True, slots=True)
class Inquiry:
    device_type: int
    removable: bool
    vendor: str
    product: str
    revision: str

    @property
    def is_mmc(self) -> bool:
        """Peripheral device type 5 = CD/DVD (MMC) device."""
        return self.device_type == 0x05

    @property
    def name(self) -> str:
        return f"{self.vendor} {self.product}".strip()


def _ascii(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").replace("\x00", " ").strip()


def parse_inquiry(data: bytes) -> Inquiry:
    if len(data) < 36:
        raise ValueError(f"INQUIRY response too short ({len(data)} bytes)")
    return Inquiry(
        device_type=data[0] & 0x1F,
        removable=bool(data[1] & 0x80),
        vendor=_ascii(data[8:16]),
        product=_ascii(data[16:32]),
        revision=_ascii(data[32:36]),
    )


@dataclass(frozen=True, slots=True)
class Capabilities:
    """From the CD/DVD Capabilities and Mechanical Status mode page (0x2A)."""

    cdda_supported: bool
    accurate_stream: bool
    c2_pointers: bool
    isrc: bool
    upc: bool
    rw_subchannel: bool
    can_eject: bool
    can_lock: bool
    max_read_kbps: int
    current_read_kbps: int

    @property
    def max_read_speed_x(self) -> float:
        return self.max_read_kbps / KBPS_1X if self.max_read_kbps else 0.0


def parse_mode_page_2a(data: bytes) -> Capabilities:
    """Parse a MODE SENSE(10) response containing page 0x2A."""
    if len(data) < 8:
        raise ValueError("mode parameter header too short")
    block_desc_len = struct.unpack_from(">H", data, 6)[0]
    page = data[8 + block_desc_len :]
    if len(page) < 16 or (page[0] & 0x3F) != 0x2A:
        raise ValueError("mode page 0x2A not present in response")
    b5, b6 = page[5], page[6]
    return Capabilities(
        cdda_supported=bool(b5 & 0x01),
        accurate_stream=bool(b5 & 0x02),
        rw_subchannel=bool(b5 & 0x04),
        c2_pointers=bool(b5 & 0x10),
        isrc=bool(b5 & 0x20),
        upc=bool(b5 & 0x40),
        can_lock=bool(b6 & 0x01),
        can_eject=bool(b6 & 0x08),
        max_read_kbps=struct.unpack_from(">H", page, 8)[0],
        current_read_kbps=struct.unpack_from(">H", page, 14)[0],
    )


@dataclass(frozen=True, slots=True)
class CdReadFeature:
    """GET CONFIGURATION feature 0x001E (CD Read)."""

    current: bool
    c2_flags: bool
    cd_text: bool
    current_profile: int

    @property
    def medium(self) -> str:
        return {0x08: "CD-ROM", 0x09: "CD-R", 0x0A: "CD-RW"}.get(
            self.current_profile, f"profile 0x{self.current_profile:04x}"
        )


FEATURE_CD_READ = 0x001E


def parse_cd_read_feature(data: bytes) -> CdReadFeature | None:
    """Return the CD Read feature, or None if the drive did not report it."""
    if len(data) < 8:
        return None
    profile = struct.unpack_from(">H", data, 6)[0]
    pos = 8
    while pos + 4 <= len(data):
        code = struct.unpack_from(">H", data, pos)[0]
        add_len = data[pos + 3]
        if code == FEATURE_CD_READ and pos + 4 < len(data):
            flags = data[pos + 4] if add_len >= 1 else 0
            return CdReadFeature(
                current=bool(data[pos + 2] & 0x01),
                c2_flags=bool(flags & 0x02),
                cd_text=bool(flags & 0x01),
                current_profile=profile,
            )
        pos += 4 + add_len
    return None


_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def parse_mcn(data: bytes) -> str | None:
    """READ SUB-CHANNEL format 02h. Returns the 13-digit UPC/EAN, or None."""
    if len(data) < 22 or data[4] != 0x02 or not data[8] & 0x80:
        return None
    mcn = _ascii(data[9:22])
    if not (mcn.isdigit() and len(mcn) == 13) or set(mcn) == {"0"}:
        return None
    return mcn


def parse_isrc(data: bytes) -> str | None:
    """READ SUB-CHANNEL format 03h. Returns a validated 12-character ISRC, or None."""
    if len(data) < 21 or data[4] != 0x03 or not data[8] & 0x80:
        return None
    isrc = _ascii(data[9:21]).upper()
    return isrc if _ISRC_RE.match(isrc) else None


def is_valid_isrc(value: str) -> bool:
    return bool(_ISRC_RE.match(value))
