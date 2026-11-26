"""CD-Text pack decoding."""

from __future__ import annotations

import struct

from fakes import cdtext_packs, cdtext_response, size_info_pack

from discproof.drive import cdtext as ct


def album_packs() -> list[bytes]:
    titles = b"Kind of Blue\x00So What\x00Freddie Freeloader\x00Blue in Green\x00"
    performers = b"Miles Davis\x00\t\x00\t\x00Bill Evans\x00"
    return [
        *cdtext_packs(ct.PACK_TITLE, 0, titles, seq=0),
        *cdtext_packs(ct.PACK_PERFORMER, 0, performers, seq=10),
        size_info_pack(charset=0x00, first=1, last=3),
    ]


def test_titles_and_performers_across_pack_boundaries() -> None:
    text = ct.parse_cdtext(cdtext_response(album_packs()))
    assert text is not None
    assert text.album.title == "Kind of Blue"
    assert text.album.performer == "Miles Davis"
    assert text.tracks[1].title == "So What"
    assert text.tracks[2].title == "Freddie Freeloader"
    assert text.tracks[3].title == "Blue in Green"
    assert text.bad_packs == 0


def test_tab_means_same_as_previous() -> None:
    text = ct.parse_cdtext(cdtext_response(album_packs()))
    assert text is not None
    assert text.tracks[1].performer == "Miles Davis"
    assert text.tracks[2].performer == "Miles Davis"
    assert text.tracks[3].performer == "Bill Evans"


def test_pack_order_does_not_matter() -> None:
    text = ct.parse_cdtext(cdtext_response(reversed(album_packs())))
    assert text is not None
    assert text.tracks[2].title == "Freddie Freeloader"


def test_bad_crc_pack_dropped() -> None:
    packs = album_packs()
    corrupt = bytearray(packs[-2])  # last performer pack ("Bill Evans")
    corrupt[6] ^= 0xFF
    packs[-2] = bytes(corrupt)
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.bad_packs == 1
    assert text.album.title == "Kind of Blue"


def test_zeroed_crcs_accepted() -> None:
    packs = [p[:16] + b"\x00\x00" for p in album_packs()]
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.tracks[3].title == "Blue in Green"
    assert text.bad_packs == 0


def test_latin1_charset() -> None:
    packs = [
        *cdtext_packs(ct.PACK_TITLE, 0, "Café Tacvba\x00Déjà vu\x00".encode("latin-1")),
        size_info_pack(charset=0x00, first=1, last=1),
    ]
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.album.title == "Café Tacvba"
    assert text.tracks[1].title == "Déjà vu"


def test_isrc_and_upc() -> None:
    packs = cdtext_packs(ct.PACK_UPC_ISRC, 0, b"0724384260927\x00USSM15900113\x00")
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.album.code == "0724384260927"
    assert text.tracks[1].code == "USSM15900113"


def test_genre() -> None:
    payload = struct.pack(">H", 0x0010) + b"Jazz\x00"
    text = ct.parse_cdtext(cdtext_response(cdtext_packs(ct.PACK_GENRE, 0, payload)))
    assert text is not None
    assert text.genre_code == 0x10
    assert text.genre == "Jazz"


def test_only_block_zero_used() -> None:
    packs = [
        *cdtext_packs(ct.PACK_TITLE, 0, b"English\x00", block=0),
        *cdtext_packs(ct.PACK_TITLE, 0, b"Deutsch\x00", block=1, seq=5),
    ]
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.album.title == "English"


def test_shift_jis_double_byte() -> None:
    title = "青い".encode("cp932")
    packs = [
        *cdtext_packs(ct.PACK_TITLE, 0, title + b"\x00\x00", dbcc=True),
        size_info_pack(charset=0x80, first=1, last=1),
    ]
    text = ct.parse_cdtext(cdtext_response(packs))
    assert text is not None
    assert text.album.title == "青い"


def test_no_cdtext() -> None:
    assert ct.parse_cdtext(b"") is None
    assert ct.parse_cdtext(cdtext_response([])) is None
    assert ct.parse_cdtext(cdtext_response(cdtext_packs(ct.PACK_TITLE, 0, b"\x00\x00\x00"))) is None
