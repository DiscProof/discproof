"""High-level verification: check a rip against AccurateRip, and detect a drive's
read offset from a disc that is in the database."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from perfect_audio_copy.drive.constants import SAMPLES_PER_SECTOR
from perfect_audio_copy.drive.toc import Toc
from perfect_audio_copy.engine.extract import Extractor
from perfect_audio_copy.engine.profile import DriveProfile, ExtractionSettings, Mode
from perfect_audio_copy.engine.source import AudioSource
from perfect_audio_copy.verify.accuraterip import (
    ArDiscId,
    ArResponse,
    TrackVerdict,
    ar_checksums,
    ar_disc_id,
    match_tracks,
)
from perfect_audio_copy.verify.offsets import (
    MAX_OFFSET,
    OffsetMatch,
    TrackAudio,
    U32Array,
    find_offsets,
    window,
)


@dataclass(frozen=True, slots=True)
class ArReport:
    disc: ArDiscId
    in_database: bool
    tracks: dict[int, TrackVerdict]
    #: Other offsets at which ripped tracks match the database: pressings that differ
    #: from this disc by that many samples (the rip itself is still fine if it matched
    #: at offset 0).
    pressings: tuple[OffsetMatch, ...] = ()

    @property
    def accurate(self) -> bool:
        return (
            self.in_database and bool(self.tracks) and all(v.accurate for v in self.tracks.values())
        )

    def summary(self) -> str:
        if not self.in_database:
            return "Disc not present in the AccurateRip database"
        ok = sum(v.accurate for v in self.tracks.values())
        total = len(self.tracks)
        if ok == total:
            return f"All {total} tracks accurately ripped"
        line = f"{ok} of {total} tracks accurately ripped"
        others = [p for p in self.pressings if p.offset != 0]
        if others:
            best = others[0]
            line += (
                f"; {len(best.tracks)} track(s) match a pressing with offset "
                f"{best.offset:+d} samples"
            )
        return line


def _u32(pcm: bytes) -> U32Array:
    return np.frombuffer(pcm, dtype="<u4")


def verify_rip(
    toc: Toc,
    tracks: dict[int, bytes],
    responses: list[ArResponse] | None,
    *,
    search_pressings: bool = True,
    max_offset: int = MAX_OFFSET,
) -> ArReport:
    """Verify ripped tracks (offset-corrected PCM keyed by track number; track 0 /
    HTOA is ignored) against the database responses (None: disc not in database)."""
    disc = ar_disc_id(toc)
    audio = toc.audio_tracks
    first, last = audio[0].number, audio[-1].number
    pcm = {n: p for n, p in tracks.items() if n != 0}
    sums = {n: ar_checksums(p, first=n == first, last=n == last) for n, p in pcm.items()}
    if responses is None:
        return ArReport(disc, False, match_tracks(sums, [], first))
    verdicts = match_tracks(sums, responses, first)
    pressings: tuple[OffsetMatch, ...] = ()
    if search_pressings and not all(v.accurate for v in verdicts.values()):
        audios = []
        for n, p in pcm.items():
            prev, nxt = pcm.get(n - 1), pcm.get(n + 1)
            silence = np.zeros(max_offset, np.uint32)
            before = (
                window(_u32(prev), len(prev) // 4 - max_offset, len(prev) // 4) if prev else silence
            )
            after = window(_u32(nxt), 0, max_offset) if nxt else silence
            audios.append(TrackAudio(n, _u32(p), before, after))
        matches = find_offsets(
            audios, responses, first_track=first, last_track=last, max_offset=max_offset
        )
        pressings = tuple(m for m in matches if m.offset != 0)
    return ArReport(disc, True, verdicts, pressings)


def offset_test_tracks(toc: Toc, count: int = 3) -> list[int]:
    """Tracks to use for offset detection: the shortest ones away from the disc edges
    (the first/last tracks have skipped regions and need lead-in/out audio)."""
    audio = list(toc.audio_tracks)
    middle = audio[1:-1] if len(audio) > 2 else audio
    middle.sort(key=lambda t: toc.length(t.number))
    return sorted(t.number for t in middle[:count])


def detect_offset_from_disc(
    source: AudioSource,
    toc: Toc,
    responses: list[ArResponse],
    profile: DriveProfile,
    *,
    tracks: list[int] | None = None,
    max_offset: int = MAX_OFFSET,
) -> list[OffsetMatch]:
    """Read a few tracks with no offset correction (burst; AccurateRip itself confirms
    the data) and find the offsets at which they match the database."""
    raw = replace(profile, read_offset=0)
    extractor = Extractor(source, raw, ExtractionSettings(mode=Mode.BURST), toc=toc)
    audio = toc.audio_tracks
    margin = -(-max_offset // SAMPLES_PER_SECTOR) + 1
    lo, hi = 0, toc.audio_leadout
    audios = []
    for n in tracks or offset_test_tracks(toc):
        start, end = toc.track(n).start, toc.end(n)
        a, b = max(lo, start - margin), min(hi, end + margin)
        data, _ = extractor.read_range(a, b - a)
        stream = _u32(data)
        s0 = (start - a) * SAMPLES_PER_SECTOR
        s1 = (end - a) * SAMPLES_PER_SECTOR
        audios.append(
            TrackAudio(
                n,
                window(stream, s0, s1),
                window(stream, s0 - max_offset, s0),
                window(stream, s1, s1 + max_offset),
            )
        )
    return find_offsets(
        audios,
        responses,
        first_track=audio[0].number,
        last_track=audio[-1].number,
        max_offset=max_offset,
    )
