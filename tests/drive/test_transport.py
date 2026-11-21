"""Retry/error policy of BaseTransport and the SG_IO ABI layout."""

from __future__ import annotations

import ctypes
import os

import pytest
from fakes import FakeTransport, check_condition, sequence

from perfect_audio_copy.drive import sense as s
from perfect_audio_copy.drive.transport import RawResult, SgIoHdr, SgioTransport

TUR = bytes(6)


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit ABI check")
def test_sg_io_hdr_matches_kernel_abi() -> None:
    # sizeof(struct sg_io_hdr) on LP64 Linux
    assert ctypes.sizeof(SgIoHdr) == 88
    assert SgIoHdr.dxferp.offset == 16
    assert SgIoHdr.status.offset == 64
    assert SgIoHdr.resid.offset == 72


def test_good_status_returns_data() -> None:
    t = FakeTransport({0x12: lambda _c, _n: b"hello"})
    assert t.execute(bytes([0x12, 0, 0, 0, 5, 0]), data_in=5) == b"hello"


def test_unit_attention_is_retried() -> None:
    t = FakeTransport({0x00: sequence(check_condition(0x6, 0x28), RawResult(status=0))})
    t.execute(TUR)
    assert len(t.log) == 2
    assert t.sleeps == [0.01]


def test_becoming_ready_is_retried_until_exhausted() -> None:
    t = FakeTransport({0x00: sequence(check_condition(0x2, 0x04, 0x01))})
    with pytest.raises(s.NotReadyError):
        t.execute(TUR)
    assert len(t.log) == 4  # initial + 3 retries


def test_medium_not_present_is_not_retried() -> None:
    t = FakeTransport({0x00: sequence(check_condition(0x2, 0x3A))})
    with pytest.raises(s.MediumNotPresentError):
        t.execute(TUR)
    assert len(t.log) == 1


def test_medium_error_is_not_retried() -> None:
    t = FakeTransport({0xBE: sequence(check_condition(0x3, 0x11, 0x05))})
    with pytest.raises(s.MediumError) as exc:
        t.execute(bytes([0xBE]) + bytes(11), data_in=2352)
    assert exc.value.sense == s.Sense(3, 0x11, 5)
    assert len(t.log) == 1


def test_recovered_error_returns_data() -> None:
    rec = check_condition(0x1, 0x17)
    t = FakeTransport({0xBE: lambda _c, _n: RawResult(0x02, b"data", rec.sense)})
    assert t.execute(bytes([0xBE]) + bytes(11), data_in=4) == b"data"


def test_busy_is_retried() -> None:
    t = FakeTransport({0x00: sequence(RawResult(status=0x08), RawResult(status=0))})
    t.execute(TUR)
    assert len(t.log) == 2


def test_host_error_raises_transport_error() -> None:
    t = FakeTransport({0x00: lambda _c, _n: RawResult(status=0, host_status=0x03)})
    with pytest.raises(s.TransportError, match="host_status=0x03"):
        t.execute(TUR)


def test_driver_sense_flag_alone_is_not_a_transport_error() -> None:
    t = FakeTransport({0x00: lambda _c, _n: RawResult(status=0, driver_status=0x08)})
    t.execute(TUR)


def test_unknown_opcode_is_illegal_request() -> None:
    with pytest.raises(s.IllegalRequestError):
        FakeTransport().execute(bytes([0xEE]) + bytes(5))


def test_bidirectional_rejected() -> None:
    with pytest.raises(ValueError):
        FakeTransport().execute(TUR, data_in=4, data_out=b"x")


def test_opening_missing_device_fails_cleanly(tmp_path: os.PathLike[str]) -> None:
    with pytest.raises(s.DriveAccessError):
        SgioTransport(os.path.join(tmp_path, "sr9"))


def test_opening_non_sg_file_fails_cleanly(tmp_path: os.PathLike[str]) -> None:
    path = os.path.join(tmp_path, "regular")
    with open(path, "wb") as f:
        f.write(b"x")
    with pytest.raises(s.DriveAccessError, match="does not support SG_IO"):
        SgioTransport(path)
