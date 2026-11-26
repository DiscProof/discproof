"""Drive read offsets: the AccurateRip drive list, detection from a disc, and
pressing offsets.

Offsets use the AccurateRip "correction" convention: a drive with +X returns audio
X samples early, so the ripper reads X samples later.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser

import numpy as np
import numpy.typing as npt

from discproof.verify.accuraterip import ArResponse, v1_over_offsets

U32Array = npt.NDArray[np.uint32]

DRIVE_OFFSETS_URL = "http://www.accuraterip.com/driveoffsets.htm"
#: The AccurateRip offset search range (EAC uses the same).
MAX_OFFSET = 3000

#: INQUIRY vendor -> how the AccurateRip list names the vendor.
VENDOR_ALIASES = {
    "HL-DT-ST": "LG ELECTRONICS",
    "MATSHITA": "PANASONIC",
}


# ---------------------------------------------------------------- drive list


@dataclass(frozen=True, slots=True)
class DriveOffsetEntry:
    vendor: str
    model: str
    offset: int | None  # None: purged from the list
    submissions: int
    agreement: int  # percent


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


_OFFSET = re.compile(r"^[+-]?\d+$")


def parse_drive_offsets(html: str) -> list[DriveOffsetEntry]:
    """Rows of the drive table: "Vendor - Model", offset, submissions, agreement %."""
    p = _TableParser()
    p.feed(html)
    out: list[DriveOffsetEntry] = []
    for row in p.rows:
        if len(row) != 4 or " - " not in f" {row[0]} ":
            continue
        name, offset_s, count_s, agree_s = row
        if offset_s == "[Purged]":
            offset = None
        elif _OFFSET.match(offset_s):
            offset = int(offset_s)
        else:
            continue  # header row
        vendor, _, model = name.partition("-")
        try:
            submissions = int(count_s) if count_s else 0
            agreement = int(agree_s.rstrip("%")) if agree_s else 0
        except ValueError:
            continue
        out.append(DriveOffsetEntry(vendor.strip(), model.strip(), offset, submissions, agreement))
    return out


def _norm(s: str) -> str:
    return " ".join(s.upper().split())


def find_drive_offset(
    entries: list[DriveOffsetEntry], vendor: str, model: str
) -> DriveOffsetEntry | None:
    """Best entry for an INQUIRY vendor/product. The model must match exactly
    (ignoring case and spacing); among matches, the vendor (or its alias) wins, then
    the most submissions. Purged entries are ignored."""
    want_model = _norm(model)
    vendors = {_norm(vendor)}
    if _norm(vendor) in VENDOR_ALIASES:
        vendors.add(VENDOR_ALIASES[_norm(vendor)])
    hits = [e for e in entries if e.offset is not None and _norm(e.model) == want_model]
    if not hits:
        return None
    hits.sort(key=lambda e: (_norm(e.vendor) in vendors, e.submissions), reverse=True)
    return hits[0]


# ---------------------------------------------------------------- offset search


@dataclass(frozen=True, slots=True)
class OffsetMatch:
    offset: int
    tracks: tuple[int, ...]  # track numbers whose v1 matched at this offset
    confidence: int  # summed database confidence of those matches


@dataclass(frozen=True, slots=True)
class TrackAudio:
    """One track's samples plus ``max_offset`` samples on each side (silence where
    the disc or the rip ends)."""

    number: int
    samples: U32Array
    before: U32Array
    after: U32Array


def track_crcs(responses: list[ArResponse], index: int) -> dict[int, int]:
    """Database CRC -> best confidence for the ``index``-th audio track."""
    crcs: dict[int, int] = {}
    for r in responses:
        if index < len(r.tracks):
            e = r.tracks[index]
            crcs[e.crc] = max(crcs.get(e.crc, 0), e.confidence)
    return crcs


def find_offsets(
    tracks: list[TrackAudio],
    responses: list[ArResponse],
    *,
    first_track: int,
    last_track: int,
    max_offset: int = MAX_OFFSET,
) -> list[OffsetMatch]:
    """Offsets at which the tracks match the database, best supported first.

    For drive offset detection the audio is read without offset correction; for
    pressing offsets it is the corrected rip.
    """
    by_offset: dict[int, dict[int, int]] = defaultdict(dict)
    for t in tracks:
        crcs = track_crcs(responses, t.number - first_track)
        if not crcs:
            continue
        sweep = v1_over_offsets(
            t.samples,
            t.before,
            t.after,
            max_offset,
            first=t.number == first_track,
            last=t.number == last_track,
        )
        found = np.nonzero(np.isin(sweep, np.array(list(crcs), dtype=np.uint64)))[0]
        for i in found:
            by_offset[int(i) - max_offset][t.number] = crcs[int(sweep[i])]
    matches = [OffsetMatch(o, tuple(sorted(t)), sum(t.values())) for o, t in by_offset.items()]
    matches.sort(key=lambda m: (len(m.tracks), m.confidence, -abs(m.offset)), reverse=True)
    return matches


def window(stream: U32Array, a: int, b: int) -> U32Array:
    """stream[a:b] with silence where the range leaves the stream."""
    out = np.zeros(b - a, dtype=np.uint32)
    lo, hi = max(a, 0), min(b, len(stream))
    if hi > lo:
        out[lo - a : hi - a] = stream[lo:hi]
    return out


def offset_candidates(matches: list[OffsetMatch], *, min_tracks: int = 2) -> list[int]:
    """Offsets a disc supports: those matched by the most tracks (at least
    ``min_tracks``). Several when the database holds pressings that differ by an
    offset — then the disc alone cannot tell which is the drive's."""
    if not matches:
        return []
    most = max(len(m.tracks) for m in matches)
    if most < min_tracks:
        return []
    return sorted(m.offset for m in matches if len(m.tracks) == most)


def detected_drive_offset(matches: list[OffsetMatch], *, min_tracks: int = 2) -> int | None:
    """The drive offset if this disc alone determines it unambiguously."""
    found = offset_candidates(matches, min_tracks=min_tracks)
    return found[0] if len(found) == 1 else None
