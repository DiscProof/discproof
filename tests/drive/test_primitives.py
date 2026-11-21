"""MSF/BCD conversion, CRC, sense decoding."""

from __future__ import annotations

import pytest

from perfect_audio_copy.drive import sense as s
from perfect_audio_copy.drive.crc import cd_crc16, crc16_ccitt
from perfect_audio_copy.drive.msf import (
    bcd_to_int,
    frames_to_timestamp,
    int_to_bcd,
    lba_to_msf,
    msf_to_lba,
)


class TestMsf:
    def test_lba_zero_is_two_seconds(self) -> None:
        assert msf_to_lba(0, 2, 0) == 0
        assert lba_to_msf(0) == (0, 2, 0)

    def test_pregap_is_negative(self) -> None:
        assert msf_to_lba(0, 0, 0) == -150

    @pytest.mark.parametrize("lba", [0, 1, 74, 75, 4499, 4500, 242307, 359_849])
    def test_round_trip(self, lba: int) -> None:
        assert msf_to_lba(*lba_to_msf(lba)) == lba

    def test_known_value(self) -> None:
        # 79:59:74 is the classic 80-minute disc limit
        assert lba_to_msf(359_849) == (79, 59, 74)

    def test_before_disc_rejected(self) -> None:
        with pytest.raises(ValueError):
            lba_to_msf(-151)

    @pytest.mark.parametrize(
        ("frames", "text"),
        [(0, "0:00.00"), (74, "0:00.74"), (75, "0:01.00"), (4500, "1:00.00"), (-150, "-0:02.00")],
    )
    def test_timestamp(self, frames: int, text: str) -> None:
        assert frames_to_timestamp(frames) == text

    def test_bcd(self) -> None:
        assert bcd_to_int(0x59) == 59
        assert int_to_bcd(59) == 0x59
        with pytest.raises(ValueError):
            bcd_to_int(0x5A)
        with pytest.raises(ValueError):
            int_to_bcd(100)


class TestCrc:
    def test_ccitt_check_value(self) -> None:
        # Standard check value of CRC-16/XMODEM (poly 0x1021, init 0)
        assert crc16_ccitt(b"123456789") == 0x31C3

    def test_cd_crc_is_inverted(self) -> None:
        assert cd_crc16(b"123456789") == 0x31C3 ^ 0xFFFF


class TestSense:
    def test_fixed_format(self) -> None:
        data = bytes([0x70, 0, 0x03, 0, 0, 0, 0, 10, 0, 0, 0, 0, 0x11, 0x05, 0, 0, 0, 0])
        sense = s.parse_sense(data)
        assert sense == s.Sense(0x3, 0x11, 0x05)
        assert "MEDIUM ERROR" in str(sense)
        assert "L-EC uncorrectable" in str(sense)

    def test_descriptor_format(self) -> None:
        assert s.parse_sense(bytes([0x72, 0x05, 0x24, 0x00, 0, 0, 0, 0])) == s.Sense(5, 0x24, 0)

    def test_garbage(self) -> None:
        assert s.parse_sense(b"") is None
        assert s.parse_sense(bytes([0x00] * 18)) is None

    @pytest.mark.parametrize(
        ("key", "asc", "cls"),
        [
            (0x2, 0x3A, s.MediumNotPresentError),
            (0x2, 0x04, s.NotReadyError),
            (0x3, 0x11, s.MediumError),
            (0x4, 0x00, s.HardwareError),
            (0x5, 0x24, s.IllegalRequestError),
            (0x6, 0x28, s.UnitAttentionError),
            (0xB, 0x00, s.ScsiError),
        ],
    )
    def test_error_mapping(self, key: int, asc: int, cls: type[s.ScsiError]) -> None:
        err = s.error_for(0xBE, s.Sense(key, asc, 0))
        assert type(err) is cls
        assert err.opcode == 0xBE
        assert "0xBE" in str(err)

    def test_medium_not_present_is_not_ready(self) -> None:
        assert issubclass(s.MediumNotPresentError, s.NotReadyError)
