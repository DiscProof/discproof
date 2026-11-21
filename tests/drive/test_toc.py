"""TOC parsing, track boundaries and CD-Extra handling."""

from __future__ import annotations

import pytest
from fakes import basic_toc, full_toc

from perfect_audio_copy.drive.constants import SESSION_GAP
from perfect_audio_copy.drive.toc import Toc, TocError, Track, parse_basic_toc, parse_full_toc

# The 6-track disc from the MusicBrainz disc ID documentation, offsets incl. 150 pre-gap:
# 1 6 242457 150 44942 61305 72755 96360 130485
MB_OFFSETS = [150, 44942, 61305, 72755, 96360, 130485]
MB_LEADOUT = 242457
AUDIO = 0x0
DATA = 0x4


def mb_tracks() -> list[tuple[int, int, int]]:
    return [(i + 1, off - 150, AUDIO) for i, off in enumerate(MB_OFFSETS)]


def test_full_toc_single_session() -> None:
    toc = parse_full_toc(full_toc([(1, mb_tracks(), MB_LEADOUT - 150)]))
    assert toc.source == "full"
    assert (toc.first, toc.last, toc.sessions) == (1, 6, 1)
    assert [t.start for t in toc.tracks] == [o - 150 for o in MB_OFFSETS]
    assert toc.leadout == MB_LEADOUT - 150
    assert toc.audio_leadout == toc.leadout
    assert toc.length(1) == 44942 - 150
    assert toc.end(6) == MB_LEADOUT - 150
    assert not toc.is_enhanced


def test_basic_and_full_toc_agree() -> None:
    full = parse_full_toc(full_toc([(1, mb_tracks(), MB_LEADOUT - 150)]))
    basic = parse_basic_toc(basic_toc(mb_tracks(), MB_LEADOUT - 150))
    assert full.tracks == basic.tracks
    assert full.leadout == basic.leadout


def test_track_flags() -> None:
    toc = parse_full_toc(full_toc([(1, [(1, 0, 0x1), (2, 1000, 0x2), (3, 2000, DATA)], 3000)]))
    assert toc.track(1).preemphasis
    assert not toc.track(1).copy_permitted
    assert toc.track(2).copy_permitted
    assert toc.track(3).is_data
    assert not toc.track(3).preemphasis


def test_enhanced_cd_full_toc() -> None:
    audio = [(1, 0, AUDIO), (2, 20000, AUDIO)]
    session1_leadout = 40000
    data_start = session1_leadout + SESSION_GAP
    toc = parse_full_toc(
        full_toc([(1, audio, session1_leadout), (2, [(3, data_start, DATA)], data_start + 5000)])
    )
    assert toc.is_enhanced
    assert toc.sessions == 2
    assert toc.track(3).session == 2
    assert toc.end(2) == session1_leadout  # not the data track's start
    assert toc.audio_leadout == session1_leadout
    assert toc.leadout == data_start + 5000
    assert [t.number for t in toc.audio_tracks] == [1, 2]


def test_enhanced_cd_basic_toc_infers_session() -> None:
    data_start = 40000 + SESSION_GAP
    toc = parse_basic_toc(
        basic_toc([(1, 0, AUDIO), (2, 20000, AUDIO), (3, data_start, DATA)], data_start + 5000)
    )
    assert toc.is_enhanced
    assert toc.audio_leadout == 40000
    assert toc.length(2) == 20000


def test_mixed_mode_data_first() -> None:
    toc = parse_basic_toc(basic_toc([(1, 0, DATA), (2, 30000, AUDIO)], 50000))
    assert toc.data_first
    assert not toc.is_enhanced
    assert toc.audio_leadout == 50000


def test_track_lookup_errors() -> None:
    toc = parse_full_toc(full_toc([(1, mb_tracks(), MB_LEADOUT - 150)]))
    with pytest.raises(KeyError):
        toc.track(7)


@pytest.mark.parametrize(
    ("tracks", "leadouts"),
    [
        ((), ((1, 100),)),
        ((Track(1, 0, 0), Track(3, 10, 0)), ((1, 100),)),  # gap in numbering
        ((Track(1, 50, 0), Track(2, 10, 0)), ((1, 100),)),  # decreasing starts
        ((Track(1, 0, 0),), ((1, 0),)),  # lead-out not after last track
        ((Track(1, 0, 0),), ()),  # no lead-out
    ],
)
def test_inconsistent_toc_rejected(
    tracks: tuple[Track, ...], leadouts: tuple[tuple[int, int], ...]
) -> None:
    with pytest.raises(TocError):
        Toc(tracks=tracks, session_leadouts=leadouts)


def test_truncated_responses() -> None:
    with pytest.raises(TocError):
        parse_full_toc(b"\x00")
    with pytest.raises(TocError):
        parse_full_toc(b"\x00\x02\x01\x01")  # header only
    with pytest.raises(TocError):
        parse_basic_toc(b"\x00\x0a\x01\x01" + bytes([0, 0x10, 1, 0, 0, 0, 0, 0]))  # no lead-out
