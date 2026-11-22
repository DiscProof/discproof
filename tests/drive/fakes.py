"""Byte-exact fakes for MMC responses, so the drive layer is testable without hardware."""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from perfect_audio_copy.drive.constants import C2_SIZE, SECTOR_SIZE, SUBQ_SIZE
from perfect_audio_copy.drive.crc import cd_crc16
from perfect_audio_copy.drive.msf import int_to_bcd, lba_to_msf
from perfect_audio_copy.drive.transport import BaseTransport, RawResult

Handler = Callable[[bytes, int], RawResult | bytes]


def check_condition(key: int, asc: int = 0, ascq: int = 0) -> RawResult:
    sense = bytearray(18)
    sense[0] = 0x70
    sense[2] = key
    sense[7] = 10
    sense[12] = asc
    sense[13] = ascq
    return RawResult(status=0x02, sense=bytes(sense), driver_status=0x08)


@dataclass
class FakeTransport(BaseTransport):
    """Dispatches CDBs by opcode to handlers. Unhandled opcodes -> ILLEGAL REQUEST 20/00."""

    handlers: dict[int, Handler] = field(default_factory=dict)
    is_writable: bool = True
    log: list[bytes] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(retries=3, retry_delay_s=0.01, sleep=self.sleeps.append)

    @property
    def writable(self) -> bool:
        return self.is_writable

    def _submit(
        self, cdb: bytes, data_in: int, data_out: bytes | None, timeout_s: float
    ) -> RawResult:
        self.log.append(bytes(cdb))
        handler = self.handlers.get(cdb[0])
        if handler is None:
            return check_condition(0x5, 0x20)
        result = handler(bytes(cdb), data_in)
        if isinstance(result, RawResult):
            return result
        return RawResult(status=0, data=result[:data_in] if data_in else b"")

    def opcodes(self) -> list[int]:
        return [c[0] for c in self.log]


def sequence(*results: RawResult | bytes) -> Handler:
    """Handler returning successive results (the last one repeats)."""
    items = list(results)

    def handler(_cdb: bytes, _n: int) -> RawResult | bytes:
        return items.pop(0) if len(items) > 1 else items[0]

    return handler


# ------------------------------------------------------------------- builders


def inquiry_data(
    vendor: str = "PLEXTOR",
    product: str = "DVDR   PX-760A",
    rev: str = "1.07",
    device_type: int = 5,
) -> bytes:
    data = bytearray(96)
    data[0] = device_type
    data[1] = 0x80
    data[4] = 91
    data[8:16] = vendor.ljust(8).encode()[:8]
    data[16:32] = product.ljust(16).encode()[:16]
    data[32:36] = rev.ljust(4).encode()[:4]
    return bytes(data)


def mode_page_2a(*, accurate: bool = True, c2: bool = True, max_kbps: int = 7056) -> bytes:
    page = bytearray(28)
    page[0] = 0x2A
    page[1] = 26
    page[5] = 0x01 | (0x02 if accurate else 0) | (0x10 if c2 else 0) | 0x20 | 0x40
    page[6] = 0x01 | 0x08 | (0x01 << 5)
    struct.pack_into(">H", page, 8, max_kbps)
    struct.pack_into(">H", page, 14, max_kbps)
    header = struct.pack(">HBBHH", 6 + len(page), 0, 0, 0, 0)
    return header + bytes(page)


def full_toc(sessions: Iterable[tuple[int, list[tuple[int, int, int]], int]]) -> bytes:
    """sessions: (session, [(track, lba, control)], leadout_lba)."""
    descs = bytearray()
    sessions = list(sessions)

    def desc(session: int, ctrl: int, point: int, pmin: int, psec: int, pframe: int) -> bytes:
        return bytes([session, 0x10 | ctrl, 0, point, 0, 0, 0, 0, pmin, psec, pframe])

    for session, tracks, leadout in sessions:
        ctrl0 = tracks[0][2]
        descs += desc(session, ctrl0, 0xA0, tracks[0][0], 0x00, 0)
        descs += desc(session, tracks[-1][2], 0xA1, tracks[-1][0], 0, 0)
        descs += desc(session, ctrl0, 0xA2, *lba_to_msf(leadout))
        for number, lba, ctrl in tracks:
            descs += desc(session, ctrl, number, *lba_to_msf(lba))
        descs += bytes([session, 0x50, 0, 0xB0, 0, 0, 0, 0, 0, 0, 0])  # ADR 5 noise
    first, last = sessions[0][0], sessions[-1][0]
    return struct.pack(">HBB", len(descs) + 2, first, last) + bytes(descs)


def basic_toc(tracks: list[tuple[int, int, int]], leadout: int) -> bytes:
    body = bytearray()
    for number, lba, ctrl in [*tracks, (0xAA, leadout, tracks[-1][2])]:
        body += bytes([0, 0x10 | ctrl, number, 0]) + struct.pack(">i", lba)
    return struct.pack(">HBB", len(body) + 2, tracks[0][0], tracks[-1][0]) + bytes(body)


def cdtext_packs(
    pack_type: int,
    first_track: int,
    text: bytes,
    *,
    seq: int = 0,
    block: int = 0,
    dbcc: bool = False,
) -> list[bytes]:
    """Split a NUL-separated text stream into CRC-protected 18-byte packs."""
    packs = []
    for pos in range(0, len(text), 12):
        # Byte 1 = track of the first character in this pack, as real encoders write it.
        track = first_track + text[:pos].count(b"\x00")
        chunk = text[pos : pos + 12].ljust(12, b"\x00")
        flags = (0x80 if dbcc else 0) | (block << 4)
        body = bytes([pack_type, track, seq + len(packs), flags]) + chunk
        packs.append(body + struct.pack(">H", cd_crc16(body)))
    return packs


def size_info_pack(charset: int = 0x00, first: int = 1, last: int = 3, seq: int = 0x40) -> bytes:
    body = bytes([0x8F, 0, seq, 0]) + bytes([charset, first, last, 0]) + bytes(8)
    return body + struct.pack(">H", cd_crc16(body))


def cdtext_response(packs: Iterable[bytes]) -> bytes:
    payload = b"".join(packs)
    return struct.pack(">HBB", len(payload) + 2, 0, 0) + payload


def subchannel_mcn(mcn: str | None) -> bytes:
    data = bytearray(24)
    struct.pack_into(">H", data, 2, 20)
    data[4] = 0x02
    if mcn:
        data[8] = 0x80
        data[9:22] = mcn.encode()
    return bytes(data)


def subchannel_isrc(track: int, isrc: str | None) -> bytes:
    data = bytearray(24)
    struct.pack_into(">H", data, 2, 20)
    data[4] = 0x03
    data[6] = track
    if isrc:
        data[8] = 0x80
        data[9:21] = isrc.encode()
    return bytes(data)


def q_record(
    track: int, index: int, rel_frames: int, abs_lba: int, *, ctrl: int = 0, crc: bool = True
) -> bytes:
    m, rest = divmod(rel_frames, 75 * 60)
    s, f = divmod(rest, 75)
    am, as_, af = lba_to_msf(abs_lba)
    body = bytes(
        [
            (ctrl << 4) | 1,
            track if track == 0xAA else int_to_bcd(track),
            int_to_bcd(index),
            int_to_bcd(m),
            int_to_bcd(s),
            int_to_bcd(f),
            0,
            int_to_bcd(am),
            int_to_bcd(as_),
            int_to_bcd(af),
        ]
    )
    return body + (struct.pack(">H", cd_crc16(body)) if crc else b"\x00\x00") + bytes(4)


def sector_audio(lba: int) -> bytes:
    """Deterministic, LBA-unique audio content for one sector."""
    return struct.pack(">i", lba) * (SECTOR_SIZE // 4)


def read_cd_handler(*, c2_errors: dict[int, int] | None = None, q_first: bool = False) -> Handler:
    """Serves READ CD from a synthetic disc; honours error-field and Q selection.

    ``q_first`` mimics drives that send Q before the C2 field instead of after it."""
    c2_errors = c2_errors or {}

    def handler(cdb: bytes, _n: int) -> bytes:
        lba = struct.unpack_from(">i", cdb, 2)[0]
        count = int.from_bytes(cdb[6:9], "big")
        want_c2 = (cdb[9] >> 1) & 0x3 == 1
        want_q = cdb[10] == 0x02
        out = bytearray()
        for i in range(count):
            out += sector_audio(lba + i)
            q = q_record(1, 1, lba + i, lba + i)[:SUBQ_SIZE]
            if want_q and q_first:
                out += q
            if want_c2:
                c2 = bytearray(C2_SIZE)
                for bit in range(c2_errors.get(lba + i, 0)):
                    c2[bit // 8] |= 0x80 >> (bit % 8)
                out += c2
            if want_q and not q_first:
                out += q
        return bytes(out)

    return handler
