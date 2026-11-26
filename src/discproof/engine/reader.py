"""Sector reading for the extraction modes.

:class:`Reader` turns "give me drive sectors [lba, lba+count)" into one
:class:`Block`, hiding:

* batching and shrinking the batch when the host/drive rejects a transfer length,
* falling back to single-sector reads when a batch hits a MEDIUM ERROR,
* sectors the drive cannot address (lead-in/lead-out without overread, data tracks),
  which come back as silence and are marked ``filled``,
* defeating the drive cache between reads,
* for drives without accurate stream, reading extra ``sync_sectors`` on each side and
  aligning the result against audio that is already known (paranoia-style), or, when
  nothing is known yet, against the majority of several reads.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from discproof.drive.constants import BYTES_PER_SAMPLE, SAMPLES_PER_SECTOR, SECTOR_SIZE
from discproof.drive.sense import IllegalRequestError, MediumError, TransportError
from discproof.drive.toc import Toc
from discproof.engine.profile import CacheDefeat, DriveProfile
from discproof.engine.source import AudioSource

BoolArray = npt.NDArray[np.bool_]
U32Array = npt.NDArray[np.uint32]

#: Finding the true position of a block on a jittery drive by majority: stop once one
#: position has ANCHOR_VOTES reads and leads every other, give up after ANCHOR_MAX_READS.
ANCHOR_VOTES = 3
ANCHOR_MAX_READS = 16
#: Sectors outside the audio area a drive with overread support is asked for.
OVERREAD_LIMIT = 75

#: Returns known-good audio for a drive sector, or None.
KnownLookup = Callable[[int], bytes | None]


@dataclass(slots=True)
class Block:
    """One read of drive sectors [lba, lba+count) after alignment."""

    lba: int
    count: int
    audio: bytes
    #: The sector could not be read (MEDIUM ERROR or alignment failure).
    failed: BoolArray
    #: The drive flagged C2 errors in the sector (None: C2 not requested).
    c2: BoolArray | None
    #: The sector is outside what the drive can address; ``audio`` holds silence.
    filled: BoolArray

    def sector(self, i: int) -> bytes:
        return self.audio[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]

    @property
    def bad(self) -> BoolArray:
        """Sectors that did not read cleanly (failed or C2-flagged)."""
        return self.failed | self.c2 if self.c2 is not None else self.failed.copy()


@dataclass(slots=True)
class _Raw:
    samples: U32Array
    failed: BoolArray
    c2: BoolArray | None  # per audio byte
    filled: BoolArray

    @property
    def valid(self) -> BoolArray:
        """Per sample: actually read from the disc (not failed, not silence-filled)."""
        return np.repeat(~(self.failed | self.filled), SAMPLES_PER_SECTOR)


class Reader:
    def __init__(self, source: AudioSource, toc: Toc, profile: DriveProfile) -> None:
        self.source = source
        self.toc = toc
        self.profile = profile
        self.batch = max(1, profile.batch_sectors)
        # Only ask for C2 when the engine will act on it.
        self.use_c2 = profile.use_c2
        audio_end = toc.audio_leadout
        self.lo = -OVERREAD_LIMIT if profile.overread_leadin else 0
        hi = audio_end
        if profile.overread_leadout and not toc.is_enhanced:
            hi = audio_end + OVERREAD_LIMIT
        self.hi = hi
        self._data = [(t.start, toc.end(t.number)) for t in toc.tracks if t.is_data]
        self.reads = 0  # READ CD commands that returned data

    # ------------------------------------------------------------ public

    def addressable(self, lba: int) -> bool:
        if not self.lo <= lba < self.hi:
            return False
        return not any(a <= lba < b for a, b in self._data)

    def read(self, lba: int, count: int, known: KnownLookup | None = None) -> Block:
        """Read [lba, lba+count). Does not defeat the cache; call :meth:`defeat_cache`."""
        if self.profile.accurate_stream:
            return self._block(lba, count, self._raw(lba, count))
        return self._read_jittery(lba, count, known or (lambda _lba: None))

    def defeat_cache(self, lba: int, count: int) -> None:
        """Make the next read of [lba, lba+count) come from the disc, not the cache."""
        if not self.profile.caches_audio:
            return
        if self.profile.cache_defeat is CacheDefeat.FUA and self.source.flush_cache(lba):
            return
        far = self._far_lba(lba, count)
        if far is None:
            return
        with contextlib.suppress(MediumError, IllegalRequestError, TransportError):
            self.source.read_cd(far, 1)

    # ------------------------------------------------------------ raw reads

    def _far_lba(self, lba: int, count: int) -> int | None:
        window = self.profile.cache_sectors + self.batch
        far = seek_away_lba(lba, count, window, max(self.lo, 0), self.toc.audio_leadout)
        return far if self.addressable(far) else None

    def _raw(self, lba: int, count: int) -> _Raw:
        samples = np.zeros(count * SAMPLES_PER_SECTOR, dtype=np.uint32)
        failed = np.zeros(count, dtype=np.bool_)
        filled = np.array([not self.addressable(lba + i) for i in range(count)], dtype=np.bool_)
        c2 = np.zeros(count * SECTOR_SIZE, dtype=np.bool_) if self.use_c2 else None
        i = 0
        while i < count:
            if filled[i]:
                i += 1
                continue
            n = 1
            while i + n < count and n < self.batch and not filled[i + n]:
                n += 1
            done = self._read_run(lba + i, n, samples=samples, failed=failed, c2=c2, at=i)
            if done == 0:  # drive refused C2: carry on without it
                c2 = None
                continue
            i += done
        return _Raw(samples, failed, c2, filled)

    def _read_run(
        self,
        lba: int,
        n: int,
        *,
        samples: U32Array,
        failed: BoolArray,
        c2: BoolArray | None,
        at: int,
    ) -> int:
        """Read up to ``n`` addressable sectors into the buffers at sector ``at``.
        Returns how many sectors were consumed."""
        while True:
            try:
                r = self.source.read_cd(lba, n, c2=c2 is not None)
            except MediumError:
                if n == 1:
                    failed[at] = True
                    return 1
                for k in range(n):  # find the bad sector(s) one by one
                    self._read_run(lba + k, 1, samples=samples, failed=failed, c2=c2, at=at + k)
                return n
            except (IllegalRequestError, TransportError) as exc:
                out_of_range = isinstance(exc, IllegalRequestError) and (
                    exc.sense is not None and exc.sense.asc == 0x21
                )
                if n == 1 or out_of_range:
                    if c2 is not None and not out_of_range:
                        # Drive refuses C2 on this read: stop asking for it.
                        self.use_c2 = False
                        return 0
                    failed[at : at + n] = True
                    return n
                n = max(1, n // 2)
                self.batch = n
                continue
            self.reads += 1
            s = at * SAMPLES_PER_SECTOR
            samples[s : s + n * SAMPLES_PER_SECTOR] = np.frombuffer(r.audio, dtype="<u4")
            if c2 is not None and r.c2 is not None:
                bits = np.unpackbits(np.frombuffer(r.c2, dtype=np.uint8)).astype(np.bool_)
                c2[at * SECTOR_SIZE : (at + n) * SECTOR_SIZE] = bits
            return n

    @staticmethod
    def _block(lba: int, count: int, raw: _Raw) -> Block:
        c2 = None
        if raw.c2 is not None:
            c2 = raw.c2.reshape(count, SECTOR_SIZE).any(axis=1)
        return Block(lba, count, raw.samples.tobytes(), raw.failed, c2, raw.filled)

    # ------------------------------------------------------------ jitter

    def _read_jittery(self, lba: int, count: int, known: KnownLookup) -> Block:
        """Every READ command can land somewhere else, so align per command: split the
        region into pieces that fit one command together with their sync margins."""
        pad = self.profile.sync_sectors
        got: dict[int, bytes] = {}

        def lookup(x: int) -> bytes | None:
            return got.get(x) or known(x)

        blocks = []
        pos = lba
        while pos < lba + count:
            step = max(1, self.batch - 2 * pad)
            n = min(step, lba + count - pos)
            b = self._read_aligned(pos, n, lookup)
            for i in range(n):
                if not (b.failed[i] or b.filled[i]):
                    got[pos + i] = b.sector(i)
            blocks.append(b)
            pos += n
        if len(blocks) == 1:
            return blocks[0]
        c2s = [b.c2 for b in blocks]
        return Block(
            lba,
            count,
            b"".join(b.audio for b in blocks),
            np.concatenate([b.failed for b in blocks]),
            None if any(c is None for c in c2s) else np.concatenate(c2s),  # type: ignore[arg-type]
            np.concatenate([b.filled for b in blocks]),
        )

    def _read_aligned(self, lba: int, count: int, known: KnownLookup) -> Block:
        pad = self.profile.sync_sectors
        ref = _known_samples(known, lba - pad, count + 2 * pad)
        raw = self._raw(lba - pad, count + 2 * pad)
        matches = self._matches(raw, ref) if ref is not None else []
        if ref is not None and not matches:
            return self._failed_block(lba, count)
        if len(matches) == 1 or (matches and _constant(raw)):
            return self._cut(lba, count, raw, matches[0])
        # Nothing known yet, or the known audio is silence that fits many shifts while
        # this read is not: find the true position by majority instead.
        shift = self._anchor(lba, count, raw)
        if shift is None:
            return self._failed_block(lba, count)
        return self._cut(lba, count, raw, shift)

    def _anchor(self, lba: int, count: int, first: _Raw) -> int | None:
        """Re-read until most reads agree on one position and assume that is the true
        one (jitter is the exception, not the rule). Returns ``first``'s shift, or None
        if no clear majority emerged."""
        pad = self.profile.sync_sectors
        votes: Counter[int] = Counter({0: 1})  # relative to first
        for _ in range(ANCHOR_MAX_READS - 1):
            ranked = votes.most_common(2)
            lead = ranked[0][1]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0
            if lead >= ANCHOR_VOTES and lead > runner_up:
                break
            self.defeat_cache(lba - pad, count + 2 * pad)
            r = self._raw(lba - pad, count + 2 * pad)
            # r[i] == first[i + d]  =>  d = shift(r) - shift(first)
            found = self._matches(r, (first.samples, first.valid))
            if found:
                votes[found[0]] += 1
        ranked = votes.most_common(2)
        if ranked[0][1] < ANCHOR_VOTES or (len(ranked) > 1 and ranked[1][1] == ranked[0][1]):
            return None
        # The majority read the true position (shift 0), so first is -majority off.
        return -ranked[0][0]

    def _matches(self, raw: _Raw, ref: tuple[U32Array, BoolArray]) -> list[int]:
        """Shifts ``s`` with raw[i] == ref[i + s] wherever both are known, smallest |s|
        first. Stops at two: more than one match means the overlap is ambiguous
        (constant audio such as silence)."""
        values, mask = ref
        samples, valid = raw.samples, raw.valid
        limit = self.profile.max_jitter
        n = len(samples)
        matches: list[int] = []
        for s in sorted(range(-limit, limit + 1), key=abs):
            a, b = max(0, -s), min(n, len(values) - s)
            if b - a < SAMPLES_PER_SECTOR:
                continue
            m = mask[a + s : b + s] & valid[a:b]
            if m.sum() < SAMPLES_PER_SECTOR // 2:
                continue
            if np.array_equal(samples[a:b][m], values[a + s : b + s][m]):
                matches.append(s)
                if len(matches) > 1:
                    break
        return matches

    def _cut(self, lba: int, count: int, raw: _Raw, shift: int) -> Block:
        """raw covers nominal sectors [lba-pad, ...) and is ``shift`` samples off:
        raw[i] holds stream sample (lba-pad)*588 + i + shift."""
        pad = self.profile.sync_sectors
        start = pad * SAMPLES_PER_SECTOR - shift
        n = count * SAMPLES_PER_SECTOR
        samples = raw.samples[start : start + n]
        c2 = None
        if raw.c2 is not None:
            c2_bytes = raw.c2[start * BYTES_PER_SAMPLE : (start + n) * BYTES_PER_SAMPLE]
            c2 = c2_bytes.reshape(count, SECTOR_SIZE).any(axis=1)
        # A sector failed if any raw sector it overlaps failed.
        failed = np.zeros(count, dtype=np.bool_)
        for i in range(count):
            a = (start + i * SAMPLES_PER_SECTOR) // SAMPLES_PER_SECTOR
            b = (start + (i + 1) * SAMPLES_PER_SECTOR - 1) // SAMPLES_PER_SECTOR
            failed[i] = raw.failed[a : b + 1].any()
        filled = np.array([not self.addressable(lba + i) for i in range(count)], dtype=np.bool_)
        for i in range(count):
            # An addressable sector that needs samples the drive did not return
            # (the read landed partly beyond the disc edge) is incomplete.
            a = (start + i * SAMPLES_PER_SECTOR) // SAMPLES_PER_SECTOR
            b = (start + (i + 1) * SAMPLES_PER_SECTOR - 1) // SAMPLES_PER_SECTOR
            if not filled[i] and raw.filled[a : b + 1].any():
                failed[i] = True
        return Block(lba, count, samples.tobytes(), failed, c2, filled)

    def _failed_block(self, lba: int, count: int) -> Block:
        return Block(
            lba,
            count,
            bytes(count * SECTOR_SIZE),
            np.ones(count, dtype=np.bool_),
            None,
            np.array([not self.addressable(lba + i) for i in range(count)], dtype=np.bool_),
        )


def seek_away_lba(lba: int, count: int, window: int, lo: int, hi: int) -> int:
    """A sector whose read evicts [lba, lba+count) from a read-ahead cache of
    ``window`` sectors. Backwards is preferred (read-ahead runs forwards); if the disc
    is too short for a full window either way, the farthest end of the disc."""
    for c in (lba - window, lba + count + window):
        if lo <= c < hi:
            return c
    return hi - 1 if (hi - 1) - (lba + count) >= lba - lo else lo


def _constant(raw: _Raw) -> bool:
    """All samples actually read have the same value (e.g. digital silence), so the
    alignment cannot change the result."""
    values = raw.samples[raw.valid]
    return len(values) == 0 or bool((values == values[0]).all())


def _known_samples(known: KnownLookup, lba: int, count: int) -> tuple[U32Array, BoolArray] | None:
    values = np.zeros(count * SAMPLES_PER_SECTOR, dtype=np.uint32)
    mask = np.zeros(count * SAMPLES_PER_SECTOR, dtype=np.bool_)
    for i in range(count):
        data = known(lba + i)
        if data is not None:
            s = i * SAMPLES_PER_SECTOR
            values[s : s + SAMPLES_PER_SECTOR] = np.frombuffer(data, dtype="<u4")
            mask[s : s + SAMPLES_PER_SECTOR] = True
    return (values, mask) if mask.any() else None
