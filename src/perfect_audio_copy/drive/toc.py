"""Table of contents model and parsers for READ TOC formats 0 (basic) and 2 (full).

Track ends, lengths and the *audio* lead-out are derived here, including CD-Extra /
Enhanced CDs where a data track lives in a second session: the audio session ends
11400 sectors before that data track starts, and that is the lead-out AccurateRip
and MusicBrainz disc IDs are computed from.
"""

from __future__ import annotations

import itertools
import struct
from dataclasses import dataclass

from perfect_audio_copy.drive.constants import (
    CTRL_COPY_PERMITTED,
    CTRL_DATA,
    CTRL_FOUR_CHANNEL,
    CTRL_PREEMPHASIS,
    SESSION_GAP,
)
from perfect_audio_copy.drive.msf import msf_to_lba


class TocError(ValueError):
    """The TOC response is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Track:
    number: int
    start: int  # LBA of INDEX 01
    control: int
    session: int = 1

    @property
    def is_audio(self) -> bool:
        return not self.control & CTRL_DATA

    @property
    def is_data(self) -> bool:
        return bool(self.control & CTRL_DATA)

    @property
    def preemphasis(self) -> bool:
        return self.is_audio and bool(self.control & CTRL_PREEMPHASIS)

    @property
    def copy_permitted(self) -> bool:
        return bool(self.control & CTRL_COPY_PERMITTED)

    @property
    def four_channel(self) -> bool:
        return self.is_audio and bool(self.control & CTRL_FOUR_CHANNEL)


@dataclass(frozen=True, slots=True)
class Toc:
    tracks: tuple[Track, ...]
    #: (session number, lead-out LBA) for every session, ascending.
    session_leadouts: tuple[tuple[int, int], ...]
    #: "full" (READ TOC format 2) or "basic" (format 0, sessions inferred).
    source: str = "full"

    def __post_init__(self) -> None:
        if not self.tracks:
            raise TocError("TOC contains no tracks")
        if not self.session_leadouts:
            raise TocError("TOC contains no lead-out")
        numbers = [t.number for t in self.tracks]
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise TocError(f"track numbers are not consecutive: {numbers}")
        starts = [t.start for t in self.tracks]
        if any(b <= a for a, b in itertools.pairwise(starts)):
            raise TocError(f"track start addresses are not increasing: {starts}")
        if self.tracks[0].start < 0:
            raise TocError("first track starts before LBA 0")
        if self.leadout <= self.tracks[-1].start:
            raise TocError("lead-out is not after the last track")

    # ----------------------------------------------------------- accessors

    @property
    def first(self) -> int:
        return self.tracks[0].number

    @property
    def last(self) -> int:
        return self.tracks[-1].number

    @property
    def leadout(self) -> int:
        """Lead-out of the last session (end of the disc)."""
        return self.session_leadouts[-1][1]

    @property
    def sessions(self) -> int:
        return len(self.session_leadouts)

    def track(self, number: int) -> Track:
        idx = number - self.first
        if not 0 <= idx < len(self.tracks):
            raise KeyError(f"no track {number} on this disc")
        return self.tracks[idx]

    @property
    def audio_tracks(self) -> tuple[Track, ...]:
        return tuple(t for t in self.tracks if t.is_audio)

    @property
    def is_enhanced(self) -> bool:
        """CD-Extra / Enhanced CD: audio session followed by a data session."""
        return self.sessions > 1 and self.tracks[-1].is_data and self.tracks[0].is_audio

    @property
    def data_first(self) -> bool:
        """Mixed-mode CD: track 1 is data."""
        return self.tracks[0].is_data

    def _session_leadout(self, session: int) -> int | None:
        for s, lba in self.session_leadouts:
            if s == session:
                return lba
        return None

    def end(self, number: int) -> int:
        """Exclusive end LBA of a track (the next track's start, or its session lead-out)."""
        track = self.track(number)
        if number < self.last:
            nxt = self.track(number + 1)
            if nxt.session == track.session:
                return nxt.start
        lo = self._session_leadout(track.session)
        if lo is not None:
            return lo
        if number < self.last:
            return self.track(number + 1).start
        return self.leadout

    def length(self, number: int) -> int:
        """Track length in sectors."""
        return self.end(number) - self.track(number).start

    @property
    def audio_leadout(self) -> int:
        """Lead-out used for disc IDs: end of the last audio track."""
        audio = self.audio_tracks
        if not audio:
            raise TocError("disc has no audio tracks")
        return self.end(audio[-1].number)


# ----------------------------------------------------------------- parsers


def _data_len(data: bytes) -> int:
    if len(data) < 4:
        raise TocError("TOC response shorter than its header")
    return min(len(data), 2 + int.from_bytes(data[0:2], "big"))


def parse_full_toc(data: bytes) -> Toc:
    """Parse a READ TOC format 2 (full TOC, MSF) response."""
    end = _data_len(data)
    tracks: dict[int, Track] = {}
    leadouts: dict[int, int] = {}
    for pos in range(4, end - 10, 11):
        session, adr_ctrl, _tno, point = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
        adr, ctrl = adr_ctrl >> 4, adr_ctrl & 0x0F
        if adr != 1:
            continue  # ADR 5 (multisession pointers) etc. are not needed
        pmin, psec, pframe = data[pos + 8], data[pos + 9], data[pos + 10]
        if 1 <= point <= 99:
            tracks.setdefault(point, Track(point, msf_to_lba(pmin, psec, pframe), ctrl, session))
        elif point == 0xA2:
            leadouts.setdefault(session, msf_to_lba(pmin, psec, pframe))
    if not tracks:
        raise TocError("full TOC has no track entries")
    return Toc(
        tracks=tuple(tracks[n] for n in sorted(tracks)),
        session_leadouts=tuple(sorted(leadouts.items())),
        source="full",
    )


def parse_basic_toc(data: bytes) -> Toc:
    """Parse a READ TOC format 0 (LBA) response.

    Format 0 carries no session numbers; a trailing data track preceded by audio is
    assumed to be a CD-Extra second session (by far the most common layout).
    """
    end = _data_len(data)
    entries: list[tuple[int, int, int]] = []
    leadout: int | None = None
    for pos in range(4, end - 7, 8):
        ctrl = data[pos + 1] & 0x0F
        number = data[pos + 2]
        lba = struct.unpack_from(">i", data, pos + 4)[0]
        if number == 0xAA:
            leadout = lba
        elif 1 <= number <= 99:
            entries.append((number, lba, ctrl))
    if leadout is None:
        raise TocError("basic TOC has no lead-out entry")
    if not entries:
        raise TocError("basic TOC has no track entries")

    tracks = [Track(n, lba, ctrl, 1) for n, lba, ctrl in entries]
    leadouts = [(1, leadout)]
    if len(tracks) >= 2 and tracks[-1].is_data and tracks[-2].is_audio:
        data_track = tracks[-1]
        tracks[-1] = Track(data_track.number, data_track.start, data_track.control, 2)
        leadouts = [(1, data_track.start - SESSION_GAP), (2, leadout)]
    return Toc(tracks=tuple(tracks), session_leadouts=tuple(leadouts), source="basic")
