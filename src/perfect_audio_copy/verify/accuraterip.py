"""AccurateRip: disc IDs, v1/v2 track checksums, database responses and matching.

Checksum definition (as in the reference ``accuraterip-checksum`` used by whipper):
each stereo sample is one little-endian uint32 ``x``, its 1-based position in the
track is the multiplier ``m``.

* v1 = sum(m * x)                         mod 2^32
* v2 = sum(lo32(m * x) + hi32(m * x))      mod 2^32

Only multipliers ``from <= m <= to`` count: ``from`` is 5 * 588 - 1 = 2939 on the
first track of the disc (else 0) and ``to`` is the sample count, minus 5 * 588 on
the last track.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from perfect_audio_copy.drive.constants import FRAMES_PER_SECOND, MSF_OFFSET, SAMPLES_PER_SECTOR
from perfect_audio_copy.drive.toc import Toc

U32Array = npt.NDArray[np.uint32]
U64Array = npt.NDArray[np.uint64]

BASE_URL = "http://www.accuraterip.com/accuraterip/"
SKIP_FIRST = 5 * SAMPLES_PER_SECTOR - 1  # 2939
SKIP_LAST = 5 * SAMPLES_PER_SECTOR  # 2940
_MASK = 0xFFFFFFFF
_CHUNK = 1 << 20

# ---------------------------------------------------------------- disc IDs


def cddb_disc_id(toc: Toc) -> int:
    """FreeDB/CDDB disc ID over all tracks (data tracks included) and the disc lead-out."""

    def digit_sum(n: int) -> int:
        return sum(int(d) for d in str(n))

    secs = [(t.start + MSF_OFFSET) // FRAMES_PER_SECOND for t in toc.tracks]
    leadout_secs = (toc.leadout + MSF_OFFSET) // FRAMES_PER_SECOND
    checksum = sum(digit_sum(s) for s in secs) % 255
    return (checksum << 24) | ((leadout_secs - secs[0]) << 8) | len(toc.tracks)


@dataclass(frozen=True, slots=True)
class ArDiscId:
    track_count: int  # audio tracks
    id1: int
    id2: int
    cddb: int

    @property
    def filename(self) -> str:
        return f"dBAR-{self.track_count:03d}-{self.id1:08x}-{self.id2:08x}-{self.cddb:08x}.bin"

    @property
    def path(self) -> str:
        h = f"{self.id1:08x}"
        return f"{h[-1]}/{h[-2]}/{h[-3]}/{self.filename}"

    @property
    def url(self) -> str:
        return BASE_URL + self.path


def ar_disc_id(toc: Toc) -> ArDiscId:
    """AccurateRip IDs. Data tracks are left out of the sums and the track count, but
    the lead-out is the end of the whole disc (CD-Extra included), as whipper does."""
    audio = toc.audio_tracks
    id1 = id2 = 0
    for t in audio:
        id1 += t.start
        id2 += max(t.start, 1) * t.number
    id1 += toc.leadout
    id2 += toc.leadout * (len(audio) + 1)
    return ArDiscId(len(audio), id1 & _MASK, id2 & _MASK, cddb_disc_id(toc))


# ---------------------------------------------------------------- checksums


@dataclass(frozen=True, slots=True)
class ArChecksums:
    v1: int
    v2: int


def _bounds(samples: int, *, first: bool, last: bool) -> tuple[int, int]:
    lo = SKIP_FIRST if first else 0
    hi = samples - SKIP_LAST if last else samples
    return max(lo, 1), hi


def ar_checksums(pcm: bytes | U32Array, *, first: bool = False, last: bool = False) -> ArChecksums:
    """v1 and v2 for one track's PCM."""
    x = np.frombuffer(pcm, dtype="<u4") if isinstance(pcm, bytes | bytearray) else pcm
    lo, hi = _bounds(len(x), first=first, last=last)
    v1 = v2 = 0
    for start in range(lo, hi + 1, _CHUNK):
        end = min(start + _CHUNK, hi + 1)  # multipliers [start, end)
        m = np.arange(start, end, dtype=np.uint64)
        prod = x[start - 1 : end - 1].astype(np.uint64) * m
        low = prod & np.uint64(_MASK)
        v1 += int(low.sum())
        v2 += int(low.sum()) + int((prod >> np.uint64(32)).sum())
    return ArChecksums(v1 & _MASK, v2 & _MASK)


def v1_over_offsets(
    track: U32Array,
    before: U32Array,
    after: U32Array,
    max_offset: int,
    *,
    first: bool = False,
    last: bool = False,
) -> U64Array:
    """v1 of the track read ``o`` samples late, for every o in [-max_offset, max_offset].

    ``before``/``after`` are the ``max_offset`` samples around the track (silence
    where the disc ends). Result index ``o + max_offset``. One pass over the track,
    then an O(1) update per offset:

        C(o+1) = C(o) - (F-1) x(o+F) + T x(o+T+1) - S(o)
        S(o+1) = S(o) - x(o+F) + x(o+T+1)

    with S(o) the plain sum over the checksum window [F, T].
    """
    mo = max_offset
    if len(before) != mo or len(after) != mo:
        raise ValueError("before/after must hold exactly max_offset samples")
    ext = np.concatenate([before, track, after]).astype(np.uint64)
    f, t = _bounds(len(track), first=first, last=last)
    # Window at offset o: multiplier m reads ext[mo + o + m - 1].
    c0 = 0
    s0 = 0
    for start in range(f, t + 1, _CHUNK):
        end = min(start + _CHUNK, t + 1)
        seg = ext[start - 1 : end - 1]  # o = -mo  ->  ext[m - 1]
        c0 += int((seg * np.arange(start, end, dtype=np.uint64)).sum())
        s0 += int(seg.sum())
    n = 2 * mo
    out = np.empty(n + 1, dtype=np.uint64)
    out[0] = np.uint64(c0 & _MASK)
    if n == 0:
        return out
    e_lo = ext[f - 1 : f - 1 + n]  # x(o + F)   for o = -mo .. mo-1
    e_hi = ext[t : t + n]  # x(o + T + 1)
    with np.errstate(over="ignore"):
        s = np.empty(n, dtype=np.uint64)
        s[0] = np.uint64(s0 & ((1 << 64) - 1))
        np.cumsum((e_hi - e_lo)[:-1], out=s[1:])
        s[1:] += s[0]
        delta = np.uint64(t) * e_hi - np.uint64(f - 1) * e_lo - s
        np.cumsum(delta, out=out[1:])
        out[1:] += out[0]
    return out & np.uint64(_MASK)


# ---------------------------------------------------------------- responses


@dataclass(frozen=True, slots=True)
class ArEntry:
    confidence: int
    crc: int
    crc450: int  # offset-finding checksum (not used by the matcher)


@dataclass(frozen=True, slots=True)
class ArResponse:
    """One pressing's (or one checksum version's) submissions for a disc."""

    track_count: int
    id1: int
    id2: int
    cddb: int
    tracks: tuple[ArEntry, ...]


class ArFormatError(ValueError):
    pass


def parse_ar_bin(data: bytes) -> list[ArResponse]:
    """Parse a dBAR .bin: concatenated responses, little-endian, 13-byte header
    (track count, id1, id2, cddb) then 9 bytes per track (confidence, crc, crc450)."""
    out: list[ArResponse] = []
    pos = 0
    while pos < len(data):
        count = data[pos]
        size = 13 + 9 * count
        if count == 0 or pos + size > len(data):
            raise ArFormatError(f"truncated AccurateRip response at byte {pos}")
        _, id1, id2, cddb = struct.unpack_from("<BIII", data, pos)
        entries = tuple(
            ArEntry(*struct.unpack_from("<BII", data, pos + 13 + 9 * i)) for i in range(count)
        )
        out.append(ArResponse(count, id1, id2, cddb, entries))
        pos += size
    return out


def build_ar_bin(responses: list[ArResponse]) -> bytes:
    """Inverse of :func:`parse_ar_bin` (tests and fixtures)."""
    out = bytearray()
    for r in responses:
        out += struct.pack("<BIII", r.track_count, r.id1, r.id2, r.cddb)
        for e in r.tracks:
            out += struct.pack("<BII", e.confidence, e.crc, e.crc450)
    return bytes(out)


# ---------------------------------------------------------------- matching


@dataclass(frozen=True, slots=True)
class TrackVerdict:
    number: int
    checksums: ArChecksums
    v1_confidence: int | None = None  # confidence of the best response matching v1
    v2_confidence: int | None = None
    max_confidence: int = 0  # best confidence of any response for this track
    database_crcs: tuple[int, ...] = field(default=())

    @property
    def accurate(self) -> bool:
        return self.v1_confidence is not None or self.v2_confidence is not None

    @property
    def confidence(self) -> int:
        return max(self.v1_confidence or 0, self.v2_confidence or 0)

    @property
    def versions(self) -> str:
        v = [
            n for n, c in (("v1", self.v1_confidence), ("v2", self.v2_confidence)) if c is not None
        ]
        return "+".join(v)

    def describe(self) -> str:
        if self.accurate:
            return f"Accurately ripped (confidence {self.confidence}) [{self.versions}]"
        if self.max_confidence:
            return f"Not accurate (database confidence {self.max_confidence})"
        return "Not present in database"


def match_tracks(
    checksums: dict[int, ArChecksums], responses: list[ArResponse], first_track: int = 1
) -> dict[int, TrackVerdict]:
    """Compare per-track checksums with every response. Both v1 and v2 are looked up
    in every response (the database stores v2 sums as additional responses)."""
    out: dict[int, TrackVerdict] = {}
    for number, cs in checksums.items():
        idx = number - first_track
        v1c: int | None = None
        v2c: int | None = None
        max_c = 0
        crcs: list[int] = []
        for r in responses:
            if not 0 <= idx < len(r.tracks):
                continue
            e = r.tracks[idx]
            max_c = max(max_c, e.confidence)
            crcs.append(e.crc)
            if e.crc == cs.v1:
                v1c = max(v1c or 0, e.confidence)
            if e.crc == cs.v2:
                v2c = max(v2c or 0, e.confidence)
        out[number] = TrackVerdict(number, cs, v1c, v2c, max_c, tuple(crcs))
    return out
