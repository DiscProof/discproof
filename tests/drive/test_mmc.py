"""CDB layouts and response parsers."""

from __future__ import annotations

import struct

import pytest
from fakes import inquiry_data, mode_page_2a, subchannel_isrc, subchannel_mcn

from discproof.drive import mmc


class TestCdbs:
    def test_read_cd_plain(self) -> None:
        cdb = mmc.cdb_read_cd(0x01020304, 26)
        assert len(cdb) == 12
        assert cdb[0] == 0xBE
        assert cdb[1] == 0x04  # expected sector type CD-DA
        assert cdb[2:6] == bytes([1, 2, 3, 4])
        assert cdb[6:9] == bytes([0, 0, 26])
        assert cdb[9] == 0x10  # user data only
        assert cdb[10] == 0x00

    def test_read_cd_with_c2_and_q(self) -> None:
        cdb = mmc.cdb_read_cd(0, 1, error_field=mmc.ErrorField.C2, subchannel=mmc.SubChannel.Q)
        assert cdb[9] == 0x12
        assert cdb[10] == 0x02

    def test_read_cd_negative_lba_is_twos_complement(self) -> None:
        assert struct.unpack(">i", mmc.cdb_read_cd(-150, 1)[2:6])[0] == -150

    def test_read_cd_rejects_bad_count(self) -> None:
        with pytest.raises(ValueError):
            mmc.cdb_read_cd(0, 0)

    def test_read_toc(self) -> None:
        cdb = mmc.cdb_read_toc(mmc.TocFormat.FULL_TOC, 0x1234)
        assert cdb == bytes([0x43, 0, 2, 0, 0, 0, 0, 0x12, 0x34, 0])

    def test_read12_fua(self) -> None:
        cdb = mmc.cdb_read12_fua(1000)
        assert cdb[0] == 0xA8
        assert cdb[1] == 0x08
        assert struct.unpack(">I", cdb[2:6])[0] == 1000
        assert cdb[6:10] == bytes(4)

    def test_set_speed(self) -> None:
        assert mmc.speed_to_kbps(8) == 1411
        assert mmc.speed_to_kbps(None) == 0xFFFF
        assert mmc.speed_to_kbps(1000) == 0xFFFF
        cdb = mmc.cdb_set_cd_speed(1411)
        assert cdb[:6] == bytes([0xBB, 0, 0x05, 0x83, 0xFF, 0xFF])
        assert len(cdb) == 12

    def test_subchannel(self) -> None:
        assert mmc.cdb_read_subchannel(3, 24, 5) == bytes([0x42, 0, 0x40, 3, 0, 0, 5, 0, 24, 0])

    def test_eject(self) -> None:
        assert mmc.cdb_start_stop(load_eject=True, start=False) == bytes([0x1B, 1, 0, 0, 2, 0])


class TestParsers:
    def test_inquiry(self) -> None:
        inq = mmc.parse_inquiry(inquiry_data("PLEXTOR", "DVDR   PX-760A", "1.07"))
        assert inq.is_mmc
        assert inq.vendor == "PLEXTOR"
        assert inq.product == "DVDR   PX-760A"
        assert inq.revision == "1.07"
        assert inq.name == "PLEXTOR DVDR   PX-760A"

    def test_inquiry_short(self) -> None:
        with pytest.raises(ValueError):
            mmc.parse_inquiry(b"\x05" * 10)

    def test_mode_page_2a(self) -> None:
        caps = mmc.parse_mode_page_2a(mode_page_2a(accurate=True, c2=False, max_kbps=7056))
        assert caps.cdda_supported
        assert caps.accurate_stream
        assert not caps.c2_pointers
        assert caps.can_eject
        assert caps.max_read_speed_x == pytest.approx(40.0)

    def test_mode_page_with_block_descriptor(self) -> None:
        raw = bytearray(mode_page_2a())
        raw[6:8] = b"\x00\x08"
        raw[8:8] = bytes(8)
        assert mmc.parse_mode_page_2a(bytes(raw)).c2_pointers

    def test_mode_page_missing(self) -> None:
        with pytest.raises(ValueError):
            mmc.parse_mode_page_2a(bytes(8) + bytes([0x01]) + bytes(20))

    def test_cd_read_feature(self) -> None:
        data = struct.pack(">IHH", 12, 0, 0x0008)  # header: profile CD-ROM
        data += struct.pack(">HBB", 0x001E, 0x09, 4) + bytes([0x02, 0, 0, 0])
        feat = mmc.parse_cd_read_feature(data)
        assert feat is not None
        assert feat.c2_flags
        assert not feat.cd_text
        assert feat.current
        assert feat.medium == "CD-ROM"

    def test_cd_read_feature_absent(self) -> None:
        assert mmc.parse_cd_read_feature(struct.pack(">IHH", 4, 0, 8)) is None

    def test_mcn(self) -> None:
        assert mmc.parse_mcn(subchannel_mcn("0724384260927")) == "0724384260927"
        assert mmc.parse_mcn(subchannel_mcn(None)) is None
        assert mmc.parse_mcn(subchannel_mcn("0000000000000")) is None

    def test_isrc(self) -> None:
        assert mmc.parse_isrc(subchannel_isrc(1, "GBAYE0601498")) == "GBAYE0601498"
        assert mmc.parse_isrc(subchannel_isrc(1, None)) is None
        assert mmc.parse_isrc(subchannel_isrc(1, "000000000000")) is None
        assert mmc.is_valid_isrc("USRC17607839")
        assert not mmc.is_valid_isrc("US-RC1-76-07839")
