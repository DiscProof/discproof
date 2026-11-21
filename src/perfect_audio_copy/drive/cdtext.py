"""CD-Text decoding (READ TOC format 5).

CD-Text is a stream of 18-byte packs: type, track, sequence number, block/char-position,
12 payload bytes and a CRC. Payload text for one pack type is a NUL-separated string
list starting at the pack's track number; a lone TAB means "same as previous track".
Only block 0 (the first language) is decoded.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from perfect_audio_copy.drive.crc import cd_crc16

PACK_SIZE = 18
PAYLOAD = slice(4, 16)

PACK_TITLE = 0x80
PACK_PERFORMER = 0x81
PACK_SONGWRITER = 0x82
PACK_COMPOSER = 0x83
PACK_ARRANGER = 0x84
PACK_MESSAGE = 0x85
PACK_DISC_ID = 0x86
PACK_GENRE = 0x87
PACK_UPC_ISRC = 0x8E
PACK_SIZE_INFO = 0x8F

_TEXT_FIELDS = {
    PACK_TITLE: "title",
    PACK_PERFORMER: "performer",
    PACK_SONGWRITER: "songwriter",
    PACK_COMPOSER: "composer",
    PACK_ARRANGER: "arranger",
    PACK_MESSAGE: "message",
    PACK_UPC_ISRC: "code",
}

_CHARSETS = {0x00: "iso-8859-1", 0x01: "ascii", 0x80: "cp932"}


@dataclass(slots=True)
class CdTextEntry:
    title: str | None = None
    performer: str | None = None
    songwriter: str | None = None
    composer: str | None = None
    arranger: str | None = None
    message: str | None = None
    #: UPC/EAN for the album entry, ISRC for track entries.
    code: str | None = None


@dataclass(slots=True)
class CdText:
    album: CdTextEntry = field(default_factory=CdTextEntry)
    tracks: dict[int, CdTextEntry] = field(default_factory=dict)
    disc_id: str | None = None
    genre_code: int | None = None
    genre: str | None = None
    charset: str = "iso-8859-1"
    #: Packs dropped because their CRC did not match.
    bad_packs: int = 0

    def track(self, number: int) -> CdTextEntry:
        return self.tracks.setdefault(number, CdTextEntry())

    @property
    def is_empty(self) -> bool:
        entries = [self.album, *self.tracks.values()]
        return not any(getattr(e, f) for e in entries for f in _TEXT_FIELDS.values())


def _pack_crc_ok(pack: bytes) -> bool:
    return cd_crc16(pack[:16]) == int.from_bytes(pack[16:18], "big")


def _split_strings(stream: bytes, double_byte: bool) -> list[bytes]:
    sep = b"\x00\x00" if double_byte else b"\x00"
    parts: list[bytes] = []
    pos = 0
    while pos < len(stream):
        nxt = stream.find(sep, pos)
        if double_byte:  # separators must be aligned on character boundaries
            while nxt != -1 and (nxt - pos) % 2:
                nxt = stream.find(sep, nxt + 1)
        if nxt == -1:
            break  # unterminated tail is padding or a truncated string: ignore
        parts.append(stream[pos:nxt])
        pos = nxt + len(sep)
    return parts


def parse_cdtext(data: bytes) -> CdText | None:
    """Decode a READ TOC format 5 response. Returns None if there is no usable CD-Text."""
    if len(data) < 4:
        return None
    end = min(len(data), 2 + struct.unpack_from(">H", data, 0)[0])
    packs = [data[p : p + PACK_SIZE] for p in range(4, end - PACK_SIZE + 1, PACK_SIZE)]
    packs = [p for p in packs if 0x80 <= p[0] <= 0x8F]
    if not packs:
        return None

    good = [p for p in packs if _pack_crc_ok(p)]
    # Some drives return zeroed CRCs for every pack; then there is nothing to check.
    crcs_present = any(p[16:18] != b"\x00\x00" for p in packs)
    bad_packs = len(packs) - len(good) if crcs_present else 0
    usable = good if crcs_present else packs
    usable = [p for p in usable if (p[3] >> 4) & 0x07 == 0]  # block 0 only
    if not usable:
        return None

    result = CdText(bad_packs=bad_packs)
    size_info = sorted((p for p in usable if p[0] == PACK_SIZE_INFO), key=lambda p: p[2])
    if size_info:
        result.charset = _CHARSETS.get(size_info[0][4], "iso-8859-1")
    double_byte = result.charset == "cp932"

    by_type: dict[int, list[bytes]] = {}
    for p in usable:
        by_type.setdefault(p[0], []).append(p)
    for packs_of_type in by_type.values():
        packs_of_type.sort(key=lambda p: p[2])

    for pack_type, attr in _TEXT_FIELDS.items():
        type_packs = by_type.get(pack_type)
        if not type_packs:
            continue
        # The ISRC/UPC pack type is always ASCII regardless of the block charset.
        dbcs = double_byte and pack_type != PACK_UPC_ISRC and bool(type_packs[0][3] & 0x80)
        stream = b"".join(p[PAYLOAD] for p in type_packs)
        track_no = type_packs[0][1] & 0x7F
        previous: str | None = None
        for raw in _split_strings(stream, dbcs):
            charset = "ascii" if pack_type == PACK_UPC_ISRC else result.charset
            # Check the "same as previous" TAB before stripping whitespace, or it is lost.
            if raw in (b"\t", b"\t\t"):
                text = previous or ""
            else:
                text = raw.decode(charset, errors="replace").strip()
            previous = text
            entry = result.album if track_no == 0 else result.track(track_no)
            if text:
                setattr(entry, attr, text)
            track_no += 1

    if PACK_DISC_ID in by_type:
        parts = _split_strings(b"".join(p[PAYLOAD] for p in by_type[PACK_DISC_ID]), False)
        if parts and parts[0].strip():
            result.disc_id = parts[0].decode("ascii", errors="replace").strip()

    if PACK_GENRE in by_type:
        stream = b"".join(p[PAYLOAD] for p in by_type[PACK_GENRE])
        if len(stream) >= 2:
            code = struct.unpack_from(">H", stream, 0)[0]
            result.genre_code = code or None
            parts = _split_strings(stream[2:], False)
            if parts and parts[0].strip():
                result.genre = parts[0].decode(result.charset, errors="replace").strip()

    return None if result.is_empty and not result.genre and not result.disc_id else result
