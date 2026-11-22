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


class IncrementalVerifier:
    """Verify tracks as they are ripped (in disc order), so a whole disc of audio is
    never held: at most the previous track waits for the next one's first samples,
    which the pressing-offset search needs."""

    def __init__(
        self,
        toc: Toc,
        responses: list[ArResponse] | None,
        *,
        search_pressings: bool = True,
        max_offset: int = MAX_OFFSET,
    ) -> None:
        self.toc = toc
        self.disc = ar_disc_id(toc)
        self.responses = responses
        audio = toc.audio_tracks
        self.first, self.last = audio[0].number, audio[-1].number
        self.search = search_pressings and bool(responses)
        self.max_offset = max_offset
        self.verdicts: dict[int, TrackVerdict] = {}
        self._held: tuple[int, U32Array, U32Array] | None = None  # number, samples, before
        self._matches: dict[int, dict[int, int]] = {}  # offset -> {track: confidence}

    def _silence(self) -> U32Array:
        return np.zeros(self.max_offset, np.uint32)

    def add(self, number: int, pcm: bytes) -> TrackVerdict | None:
        """Verify one track; None for the hidden track (not in AccurateRip)."""
        if number == 0:
            return None
        sums = ar_checksums(pcm, first=number == self.first, last=number == self.last)
        verdict = match_tracks({number: sums}, self.responses or [], self.first)[number]
        self.verdicts[number] = verdict
        if self.search:
            samples = _u32(pcm)
            before = self._silence()
            if self._held is not None:
                held_n, held_samples, held_before = self._held
                contiguous = held_n + 1 == number
                after = window(samples, 0, self.max_offset) if contiguous else self._silence()
                self._sweep(held_n, held_samples, held_before, after)
                if contiguous:
                    n = len(held_samples)
                    before = window(held_samples, n - self.max_offset, n)
            self._held = (number, samples, before)
        return verdict

    def _sweep(self, number: int, samples: U32Array, before: U32Array, after: U32Array) -> None:
        assert self.responses is not None
        found = find_offsets(
            [TrackAudio(number, samples, before, after)],
            self.responses,
            first_track=self.first,
            last_track=self.last,
            max_offset=self.max_offset,
        )
        for m in found:
            self._matches.setdefault(m.offset, {})[number] = m.confidence

    def finish(self) -> ArReport:
        if self._held is not None:
            n, samples, before = self._held
            self._sweep(n, samples, before, self._silence())
            self._held = None
        if self.responses is None:
            return ArReport(self.disc, False, dict(self.verdicts))
        # Report other pressings only for tracks that did not verify at offset 0.
        failed = {n for n, v in self.verdicts.items() if not v.accurate}
        pressings = []
        for offset, tracks in self._matches.items():
            hit = {n: c for n, c in tracks.items() if n in failed}
            if offset != 0 and hit:
                pressings.append(OffsetMatch(offset, tuple(sorted(hit)), sum(hit.values())))
        pressings.sort(key=lambda m: (len(m.tracks), m.confidence), reverse=True)
        return ArReport(self.disc, True, dict(self.verdicts), tuple(pressings))


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
    v = IncrementalVerifier(
        toc, responses, search_pressings=search_pressings, max_offset=max_offset
    )
    for n in sorted(tracks):
        v.add(n, tracks[n])
    return v.finish()


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
