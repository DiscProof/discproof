"""Drive: the high-level API, against a byte-exact fake transport."""

from __future__ import annotations

import struct

import pytest
from fakes import (
    FakeTransport,
    Handler,
    basic_toc,
    cdtext_packs,
    cdtext_response,
    check_condition,
    full_toc,
    inquiry_data,
    mode_page_2a,
    q_record,
    read_cd_handler,
    sector_audio,
    sequence,
    subchannel_isrc,
    subchannel_mcn,
)

from discproof.drive import cdtext as ct
from discproof.drive import sense as s
from discproof.drive.constants import C2_SIZE, SECTOR_SIZE, SUBQ_SIZE
from discproof.drive.device import Drive
from discproof.drive.subchannel import QPosition, parse_q
from discproof.drive.transport import RawResult

TRACKS = [(1, 0, 0), (2, 15000, 0), (3, 30000, 0)]
LEADOUT = 45000
ISRCS = {1: "GBAYE0601498", 2: None, 3: "USSM15900113"}


def toc_handler(full: bool = True):  # type: ignore[no-untyped-def]
    def handler(cdb: bytes, _n: int) -> bytes | RawResult:
        fmt = cdb[2] & 0x0F
        if fmt == 2:
            return full_toc([(1, TRACKS, LEADOUT)]) if full else check_condition(0x5, 0x24)
        if fmt == 0:
            return basic_toc(TRACKS, LEADOUT)
        if fmt == 5:
            return cdtext_response(cdtext_packs(ct.PACK_TITLE, 0, b"Album\x00One\x00Two\x00"))
        return check_condition(0x5, 0x24)

    return handler


def subchannel_handler(cdb: bytes, _n: int) -> bytes:
    if cdb[3] == 2:
        return subchannel_mcn("0724384260927")
    return subchannel_isrc(cdb[6], ISRCS.get(cdb[6]))


def make_drive(overrides: dict[int, Handler] | None = None) -> tuple[Drive, FakeTransport]:
    handlers = {
        0x00: lambda _c, _n: RawResult(0),
        0x12: lambda _c, _n: inquiry_data(),
        0x5A: lambda _c, _n: mode_page_2a(),
        0x43: toc_handler(),
        0x42: subchannel_handler,
        0xBE: read_cd_handler(),
        0xA8: lambda _c, _n: RawResult(0),
        0xBB: lambda _c, _n: RawResult(0),
        0x1E: lambda _c, _n: RawResult(0),
        0x1B: lambda _c, _n: RawResult(0),
    }
    handlers.update(overrides or {})
    transport = FakeTransport(handlers)
    return Drive(transport, path="/dev/fake"), transport


def test_identity_and_capabilities() -> None:
    drive, _ = make_drive()
    assert drive.name == "PLEXTOR DVDR   PX-760A"
    caps = drive.capabilities()
    assert caps is not None
    assert caps.c2_pointers


def test_capabilities_unsupported() -> None:
    drive, _ = make_drive({0x5A: lambda _c, _n: check_condition(0x5, 0x24)})
    assert drive.capabilities() is None


def test_read_toc_uses_two_step_allocation() -> None:
    drive, t = make_drive()
    toc = drive.read_toc()
    assert toc.source == "full"
    assert toc.leadout == LEADOUT
    toc_cdbs = [c for c in t.log if c[0] == 0x43]
    assert struct.unpack(">H", toc_cdbs[0][7:9])[0] == 4  # header probe
    assert struct.unpack(">H", toc_cdbs[1][7:9])[0] > 4


def test_read_toc_falls_back_to_basic() -> None:
    drive, _ = make_drive({0x43: toc_handler(full=False)})
    toc = drive.read_toc()
    assert toc.source == "basic"
    assert [t.start for t in toc.tracks] == [0, 15000, 30000]


def test_read_disc_info() -> None:
    drive, _ = make_drive()
    info = drive.read_disc_info()
    assert info.mcn == "0724384260927"
    assert info.isrcs == {1: "GBAYE0601498", 3: "USSM15900113"}
    assert info.cdtext is not None
    assert info.cdtext.album.title == "Album"
    assert info.cdtext.tracks[2].title == "Two"


def test_no_cdtext_is_none() -> None:
    def no_text(cdb: bytes, n: int) -> bytes | RawResult:
        return check_condition(0x5, 0x24) if cdb[2] & 0xF == 5 else toc_handler()(cdb, n)

    drive, _ = make_drive({0x43: no_text})
    assert drive.read_cdtext() is None


def test_read_cd_plain() -> None:
    drive, _ = make_drive()
    r = drive.read_cd(100, 3)
    assert r.audio == sector_audio(100) + sector_audio(101) + sector_audio(102)
    assert r.c2 is None
    assert r.subq is None
    assert r.sector(1) == sector_audio(101)


def test_read_cd_splits_c2_and_q() -> None:
    drive, _ = make_drive({0xBE: read_cd_handler(c2_errors={101: 5})})
    r = drive.read_cd(100, 3, c2=True, subq=True)
    assert len(r.audio) == 3 * SECTOR_SIZE
    assert r.audio == b"".join(sector_audio(i) for i in (100, 101, 102))
    assert r.c2 is not None
    assert len(r.c2) == 3 * C2_SIZE
    assert r.c2_error_bits == 5
    assert r.c2_for(0) == bytes(C2_SIZE)
    assert r.subq is not None
    q = parse_q(r.subq[2])
    assert isinstance(q, QPosition)
    assert q.absolute_lba == 102
    assert q.crc_ok


@pytest.mark.parametrize("q_first", [False, True])
def test_read_cd_detects_c2_q_order(q_first: bool) -> None:
    drive, _ = make_drive({0xBE: read_cd_handler(c2_errors={101: 5}, q_first=q_first)})
    assert drive.q_before_c2 is None
    r = drive.read_cd(100, 3, c2=True, subq=True)
    assert drive.q_before_c2 is q_first
    assert r.c2_error_bits == 5
    assert r.c2_for(0) == bytes(C2_SIZE)
    assert r.subq is not None
    for i, q in enumerate(r.subq):
        pos = parse_q(q)
        assert isinstance(pos, QPosition)
        assert pos.absolute_lba == 100 + i
        assert pos.crc_ok
    # The decision sticks for later reads.
    r2 = drive.read_cd(200, 2, c2=True, subq=True)
    assert r2.c2_error_bits == 0
    assert r2.subq is not None
    pos = parse_q(r2.subq[1])
    assert isinstance(pos, QPosition) and pos.absolute_lba == 201


def test_c2_q_order_stays_undecided_without_valid_q() -> None:

    stride = SECTOR_SIZE + C2_SIZE + SUBQ_SIZE
    drive, _ = make_drive({0xBE: lambda cdb, _n: bytes(stride * int.from_bytes(cdb[6:9], "big"))})
    r = drive.read_cd(100, 2, c2=True, subq=True)
    assert drive.q_before_c2 is None
    assert r.c2_error_bits == 0


def test_short_read_is_transport_error() -> None:
    drive, _ = make_drive({0xBE: lambda _c, _n: b"\x00" * 100})
    with pytest.raises(s.TransportError, match="short read"):
        drive.read_cd(0, 1)


def test_iter_read_batches() -> None:
    drive, t = make_drive()
    chunks = list(drive.iter_read(0, 60))
    assert [c.count for c in chunks] == [26, 26, 8]
    assert [c.lba for c in chunks] == [0, 26, 52]
    assert b"".join(c.audio for c in chunks) == b"".join(sector_audio(i) for i in range(60))
    assert t.opcodes().count(0xBE) == 3


def test_iter_read_respects_host_transfer_limit() -> None:
    drive, _ = make_drive()
    drive.max_transfer_bytes = 10 * SECTOR_SIZE
    assert [c.count for c in drive.iter_read(0, 25)] == [10, 10, 5]


def test_iter_read_shrinks_batch_on_rejected_length() -> None:
    inner = read_cd_handler()

    def picky(cdb: bytes, n: int) -> bytes | RawResult:
        if int.from_bytes(cdb[6:9], "big") > 8:
            return check_condition(0x5, 0x24)
        return inner(cdb, n)

    drive, _ = make_drive({0xBE: picky})
    chunks = list(drive.iter_read(0, 30))
    assert sum(c.count for c in chunks) == 30
    assert all(c.count <= 8 for c in chunks)
    assert drive.max_batch <= 8  # remembered for later reads


def test_iter_read_lba_out_of_range_propagates() -> None:
    drive, _ = make_drive({0xBE: lambda _c, _n: check_condition(0x5, 0x21)})
    with pytest.raises(s.IllegalRequestError):
        list(drive.iter_read(LEADOUT, 5))


def test_iter_read_medium_error_propagates_with_position() -> None:
    inner = read_cd_handler()

    def scratched(cdb: bytes, n: int) -> bytes | RawResult:
        lba = struct.unpack(">i", cdb[2:6])[0]
        count = int.from_bytes(cdb[6:9], "big")
        if lba <= 40 < lba + count:
            return check_condition(0x3, 0x11, 0x05)
        return inner(cdb, n)

    drive, _ = make_drive({0xBE: scratched})
    got = []
    with pytest.raises(s.MediumError):
        for chunk in drive.iter_read(0, 60):
            got.append(chunk.lba)
    assert got == [0]  # first batch fine, second (26..51) hits LBA 40


def test_set_speed() -> None:
    drive, t = make_drive()
    assert drive.set_speed(8)
    assert t.log[-1][:4] == bytes([0xBB, 0, 0x05, 0x83])
    assert drive.set_speed(None)
    assert t.log[-1][2:4] == b"\xff\xff"


def test_set_speed_needs_writable_fd() -> None:
    drive, t = make_drive()
    t.is_writable = False
    assert not drive.set_speed(8)
    assert 0xBB not in t.opcodes()


def test_flush_cache() -> None:
    drive, t = make_drive()
    assert drive.flush_cache(1234)
    assert t.log[-1][0] == 0xA8
    drive2, _ = make_drive({0xA8: lambda _c, _n: check_condition(0x5, 0x24)})
    assert not drive2.flush_cache(1234)


def test_eject_unlocks_first() -> None:
    drive, t = make_drive()
    drive.eject()
    assert t.opcodes()[-2:] == [0x1E, 0x1B]
    assert t.log[-1][4] == 0x02


def test_wait_ready_polls_until_ready() -> None:
    clock = iter([0.0, 0.0, 1.0, 2.0])
    not_ready = check_condition(0x2, 0x04, 0x00)
    drive, t = make_drive({0x00: sequence(not_ready, not_ready, RawResult(0))})
    t.retries = 0
    drive._clock = lambda: next(clock)
    sleeps: list[float] = []
    drive._sleep = sleeps.append
    assert drive.wait_ready(timeout_s=10)
    assert len(sleeps) == 2


def test_wait_ready_no_disc() -> None:
    drive, _ = make_drive({0x00: lambda _c, _n: check_condition(0x2, 0x3A)})
    assert not drive.wait_ready()
    assert not drive.medium_present()
    assert not drive.test_unit_ready()


def test_q_parser_edge_cases() -> None:
    lead_out = parse_q(q_record(0xAA, 1, 0, 50000))
    assert isinstance(lead_out, QPosition)
    assert lead_out.track == 0xAA
    pregap = parse_q(q_record(2, 0, 150, 14850, crc=False))
    assert isinstance(pregap, QPosition)
    assert pregap.index == 0
    assert pregap.relative_frames == 150
    assert pregap.crc_ok is None
    garbage = bytearray(q_record(1, 1, 0, 0))
    garbage[2] = 0xFA  # invalid BCD
    assert parse_q(bytes(garbage)) is None
    bad_crc = bytearray(q_record(1, 1, 0, 0))
    bad_crc[5] ^= 0x01
    parsed = parse_q(bytes(bad_crc))
    assert isinstance(parsed, QPosition)
    assert parsed.crc_ok is False
