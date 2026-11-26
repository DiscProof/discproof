"""AccurateRip IDs, checksums and response handling.

Disc-ID vectors come from whipper's published test suite (test_image_table.py /
test_image_toc.py). Checksums are checked against a literal port of the reference
accuraterip-checksum loop.
"""

from __future__ import annotations

import numpy as np
import pytest

from discproof.drive.constants import CTRL_DATA
from discproof.drive.toc import Toc, Track
from discproof.verify.accuraterip import (
    ArChecksums,
    ArDiscId,
    ArEntry,
    ArFormatError,
    ArResponse,
    ar_checksums,
    ar_disc_id,
    build_ar_bin,
    cddb_disc_id,
    match_tracks,
    parse_ar_bin,
    v1_over_offsets,
)


def reference_checksums(x: list[int], first: bool, last: bool) -> tuple[int, int]:
    """Port of whipper's accuraterip-checksum.c main loop."""
    check_from = 5 * 588 - 1 if first else 0
    check_to = len(x) - (5 * 588 if last else 0)
    v1 = v2 = 0
    mul = 1
    for sample in x:
        if check_from <= mul <= check_to:
            prod = sample * mul
            v2 += (prod & 0xFFFFFFFF) + (prod >> 32)
            v1 += mul * sample
        mul += 1
    return v1 & 0xFFFFFFFF, v2 & 0xFFFFFFFF


# ---------------------------------------------------------------- disc IDs

LADYHAWKE = Toc(
    (
        *tuple(
            Track(i + 1, s, 0)
            for i, s in enumerate(
                [
                    0,
                    15537,
                    31691,
                    50866,
                    66466,
                    81202,
                    99409,
                    115920,
                    133093,
                    149847,
                    161560,
                    177682,
                ]
            )
        ),
        Track(13, 207106, CTRL_DATA, session=2),
    ),
    ((1, 207106 - 11400), (2, 210385)),
)

# Bloc Party "Silent Alarm": cd-discid offsets (LBA + 150); lead-out derived from
# whipper's expected AR id1 (the id2 check then independently confirms it).
BLOC = Toc(
    tuple(
        Track(i + 1, o - 150, 0)
        for i, o in enumerate(
            [
                15370,
                35019,
                51532,
                69190,
                84292,
                96826,
                112527,
                132448,
                148595,
                168072,
                185539,
                203331,
                222103,
            ]
        )
    ),
    ((1, 243216),),
)


def test_ladyhawke_enhanced_cd_ids() -> None:
    disc = ar_disc_id(LADYHAWKE)
    assert (disc.id1, disc.id2) == (0x0013BD5A, 0x00B8D489)
    assert disc.track_count == 12
    assert cddb_disc_id(LADYHAWKE) == 0xC60AF50D
    assert disc.path == "a/5/d/dBAR-012-0013bd5a-00b8d489-c60af50d.bin"
    assert disc.url == (
        "http://www.accuraterip.com/accuraterip/a/5/d/dBAR-012-0013bd5a-00b8d489-c60af50d.bin"
    )


def test_bloc_party_htoa_ids() -> None:
    disc = ar_disc_id(BLOC)
    assert disc.path == "e/d/2/dBAR-013-001af2de-0105994e-ad0be00d.bin"
    assert cddb_disc_id(BLOC) == 0xAD0BE00D


def test_track_at_lba_zero_counts_as_one_in_id2() -> None:
    toc = Toc((Track(1, 0, 0), Track(2, 100, 0)), ((1, 300),))
    assert ar_disc_id(toc).id2 == 1 * 1 + 100 * 2 + 300 * 3


# ---------------------------------------------------------------- checksums


@pytest.mark.parametrize(
    ("first", "last"), [(False, False), (True, False), (False, True), (True, True)]
)
def test_checksums_match_reference(first: bool, last: bool) -> None:
    rng = np.random.default_rng(7)
    x = rng.integers(0, 2**32, size=588 * 13 + 17, dtype=np.uint64).astype(np.uint32)
    got = ar_checksums(x.tobytes(), first=first, last=last)
    assert (got.v1, got.v2) == reference_checksums([int(v) for v in x], first, last)


def test_checksums_large_multipliers_do_not_overflow() -> None:
    # 20-minute track: multipliers beyond 2^25, samples near 2^32.
    n = 20 * 60 * 44100
    x = np.full(n, 0xFFFFFFFF, dtype=np.uint32)
    got = ar_checksums(x)
    total = n * (n + 1) // 2
    assert got.v1 == (total * 0xFFFFFFFF) & 0xFFFFFFFF


@pytest.mark.parametrize(("first", "last"), [(False, False), (True, False), (False, True)])
def test_v1_over_offsets_matches_brute_force(first: bool, last: bool) -> None:
    rng = np.random.default_rng(3)
    mo = 300
    whole = rng.integers(0, 2**32, size=588 * 12 + 2 * mo, dtype=np.uint64).astype(np.uint32)
    track = whole[mo:-mo]
    sweep = v1_over_offsets(track, whole[:mo], whole[-mo:], mo, first=first, last=last)
    for o in (-mo, -mo + 1, -7, 0, 1, 6, 299, mo):
        shifted = whole[mo + o : mo + o + len(track)]
        assert int(sweep[o + mo]) == ar_checksums(shifted, first=first, last=last).v1, o


# ---------------------------------------------------------------- responses


def response(crcs: list[int], conf: int, disc: ArDiscId | None = None) -> ArResponse:
    d = disc or ArDiscId(len(crcs), 0xF21C, 0x27EF8, 0x05021002)
    return ArResponse(len(crcs), d.id1, d.id2, d.cddb, tuple(ArEntry(conf, c, 0) for c in crcs))


def test_bin_roundtrip_and_layout() -> None:
    # The two responses of whipper's dBAR-002-0000f21c-00027ef8-05021002.bin fixture.
    r1 = ArResponse(
        2, 0xF21C, 0x27EF8, 0x05021002, (ArEntry(12, 0x284FC705, 1), ArEntry(20, 0x9CC1F32E, 2))
    )
    r2 = ArResponse(
        2, 0xF21C, 0x27EF8, 0x05021002, (ArEntry(7, 0xDC77F9AB, 3), ArEntry(7, 0xDD97D2C3, 4))
    )
    data = build_ar_bin([r1, r2])
    assert len(data) == 2 * (13 + 2 * 9)
    assert data[:5] == bytes([2, 0x1C, 0xF2, 0, 0])  # count, id1 little-endian
    assert parse_ar_bin(data) == [r1, r2]


def test_truncated_bin_is_rejected() -> None:
    data = build_ar_bin([response([1, 2], 3)])
    with pytest.raises(ArFormatError):
        parse_ar_bin(data[:-1])


def test_match_reports_v1_v2_and_confidence() -> None:
    # whipper TestVerifyResult: v1 sums in response 1, v2 sums in response 2.
    responses = [response([0x284FC705, 0x9CC1F32E], 12), response([0xDC77F9AB, 0xDD97D2C3], 7)]
    responses[0] = ArResponse(2, 0, 0, 0, (ArEntry(12, 0x284FC705, 0), ArEntry(20, 0x9CC1F32E, 0)))
    sums = {1: ArChecksums(0x284FC705, 0xDC77F9AB), 2: ArChecksums(0x9CC1F32E, 0xDD97D2C3)}
    got = match_tracks(sums, responses)
    assert got[1].v1_confidence == 12 and got[1].v2_confidence == 7
    assert got[2].v1_confidence == 20 and got[2].max_confidence == 20
    assert got[1].describe() == "Accurately ripped (confidence 12) [v1+v2]"


def test_mismatch_and_absent() -> None:
    responses = [response([0x284FC705, 0x9CC1F32E], 12)]
    got = match_tracks({1: ArChecksums(0xDEADBEEF, 0x89ABCDEF)}, responses)
    assert not got[1].accurate
    assert got[1].describe() == "Not accurate (database confidence 12)"
    assert match_tracks({1: ArChecksums(1, 2)}, [])[1].describe() == "Not present in database"
