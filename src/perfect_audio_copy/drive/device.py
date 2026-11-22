"""High-level optical drive API used by the extraction engine and the UI backend."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from perfect_audio_copy.drive import mmc
from perfect_audio_copy.drive.cdtext import CdText, parse_cdtext
from perfect_audio_copy.drive.constants import C2_SIZE, SECTOR_SIZE, SUBQ_SIZE
from perfect_audio_copy.drive.sense import (
    DriveAccessError,
    IllegalRequestError,
    MediumNotPresentError,
    NotReadyError,
    ScsiError,
    TransportError,
)
from perfect_audio_copy.drive.subchannel import QPosition, parse_q
from perfect_audio_copy.drive.toc import Toc, TocError, parse_basic_toc, parse_full_toc
from perfect_audio_copy.drive.transport import BaseTransport, SgioTransport

DEFAULT_BATCH = 26
_HEADER_ONLY = 4


@dataclass(frozen=True, slots=True)
class SectorRead:
    """Result of one READ CD command: ``count`` consecutive sectors from ``lba``."""

    lba: int
    count: int
    audio: bytes
    c2: bytes | None = None
    subq: tuple[bytes, ...] | None = None

    def sector(self, i: int) -> bytes:
        return self.audio[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]

    def c2_for(self, i: int) -> bytes | None:
        return None if self.c2 is None else self.c2[i * C2_SIZE : (i + 1) * C2_SIZE]

    @property
    def c2_error_bits(self) -> int:
        """Total number of audio bytes the drive flagged as C2-uncorrectable."""
        if not self.c2:
            return 0
        return int.from_bytes(self.c2, "big").bit_count()


def _q_matches(q: bytes, lba: int) -> bool:
    pos = parse_q(q)
    return isinstance(pos, QPosition) and pos.crc_ok is not False and pos.absolute_lba == lba


def _detect_q_before_c2(data: bytes, lba: int, count: int, stride: int) -> bool | None:
    """Decide from one combined C2+Q read whether the drive sent Q before C2.

    Each candidate layout scores the sectors whose Q record decodes to the expected
    absolute position. Returns None (undecided) when neither layout matches any sector.
    """
    mmc_order = q_first = 0
    for i in range(count):
        after_audio = i * stride + SECTOR_SIZE
        if _q_matches(data[after_audio + C2_SIZE : after_audio + C2_SIZE + SUBQ_SIZE], lba + i):
            mmc_order += 1
        if _q_matches(data[after_audio : after_audio + SUBQ_SIZE], lba + i):
            q_first += 1
    if mmc_order == q_first:
        return None
    return q_first > mmc_order


@dataclass(slots=True)
class DiscInfo:
    toc: Toc
    mcn: str | None = None
    isrcs: dict[int, str] = field(default_factory=dict)
    cdtext: CdText | None = None


class Drive:
    """An MMC optical drive. Create with :meth:`open` or wrap any :class:`BaseTransport`."""

    def __init__(
        self,
        transport: BaseTransport,
        *,
        path: str = "",
        max_batch: int = DEFAULT_BATCH,
        max_transfer_bytes: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.path = path
        self.max_batch = max_batch
        self.max_transfer_bytes = max_transfer_bytes
        self._clock = clock
        self._sleep = sleep
        self._inquiry: mmc.Inquiry | None = None
        # Where the drive puts Q relative to C2 when both are requested. MMC says
        # audio | C2 | Q, but some drives (e.g. HL-DT-ST GP75N) send audio | Q | C2.
        # None until a combined read has told us; see _detect_q_before_c2.
        self.q_before_c2: bool | None = None

    @classmethod
    def open(cls, path: str, *, max_batch: int = DEFAULT_BATCH) -> Drive:
        from perfect_audio_copy.drive.enumerate import max_transfer_bytes  # noqa: PLC0415

        transport = SgioTransport(path)
        drive = cls(
            transport, path=path, max_batch=max_batch, max_transfer_bytes=max_transfer_bytes(path)
        )
        try:
            info = drive.inquiry()
        except Exception:
            transport.close()
            raise
        if not info.is_mmc:
            transport.close()
            raise DriveAccessError(f"{path} is not a CD/DVD drive (device type {info.device_type})")
        return drive

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------ identity

    def inquiry(self) -> mmc.Inquiry:
        if self._inquiry is None:
            self._inquiry = mmc.parse_inquiry(
                self.transport.execute(mmc.cdb_inquiry(96), data_in=96)
            )
        return self._inquiry

    @property
    def name(self) -> str:
        return self.inquiry().name

    def capabilities(self) -> mmc.Capabilities | None:
        """Mode page 0x2A, or None if the drive refuses it."""
        try:
            data = self.transport.execute(mmc.cdb_mode_sense10(0x2A, 256), data_in=256)
            return mmc.parse_mode_page_2a(data)
        except (IllegalRequestError, ValueError):
            return None

    def cd_read_feature(self) -> mmc.CdReadFeature | None:
        try:
            data = self.transport.execute(
                mmc.cdb_get_configuration(mmc.FEATURE_CD_READ, 64), data_in=64
            )
        except IllegalRequestError:
            return None
        return mmc.parse_cd_read_feature(data)

    # ------------------------------------------------------------ readiness

    def test_unit_ready(self) -> bool:
        """True if a medium is loaded and ready; False if not ready or no medium."""
        try:
            self.transport.execute(mmc.cdb_test_unit_ready())
        except NotReadyError:
            return False
        return True

    def medium_present(self) -> bool:
        try:
            self.transport.execute(mmc.cdb_test_unit_ready())
        except MediumNotPresentError:
            return False
        except NotReadyError:
            return True  # spinning up
        return True

    def wait_ready(self, timeout_s: float = 30.0, poll_s: float = 0.5) -> bool:
        """Wait for the drive to become ready (disc spun up). False on timeout/no disc."""
        deadline = self._clock() + timeout_s
        while True:
            try:
                self.transport.execute(mmc.cdb_test_unit_ready())
                return True
            except MediumNotPresentError:
                return False
            except NotReadyError:
                if self._clock() >= deadline:
                    return False
                self._sleep(poll_s)

    # ------------------------------------------------------------ disc layout

    def _read_variable(self, build: Callable[[int], bytes], max_len: int = 0xFFFE) -> bytes:
        """Two-step read of a response whose first 2 bytes give its remaining length."""
        head = self.transport.execute(build(_HEADER_ONLY), data_in=_HEADER_ONLY)
        if len(head) < 2:
            raise TocError("response header truncated")
        total = min(max_len, 2 + int.from_bytes(head[:2], "big"))
        if total <= len(head):
            return head
        return self.transport.execute(build(total), data_in=total)

    def read_toc(self) -> Toc:
        """Full TOC (sessions, CD-Extra) with fallback to the basic TOC."""
        try:
            data = self._read_variable(lambda n: mmc.cdb_read_toc(mmc.TocFormat.FULL_TOC, n))
            return parse_full_toc(data)
        except (IllegalRequestError, TocError):
            data = self._read_variable(lambda n: mmc.cdb_read_toc(mmc.TocFormat.TOC, n))
            return parse_basic_toc(data)

    def read_cdtext(self) -> CdText | None:
        try:
            data = self._read_variable(lambda n: mmc.cdb_read_toc(mmc.TocFormat.CD_TEXT, n))
        except (ScsiError, TocError):
            return None  # most drives answer ILLEGAL REQUEST when there is no CD-Text
        return parse_cdtext(data)

    def read_mcn(self) -> str | None:
        try:
            return mmc.parse_mcn(self.transport.execute(mmc.cdb_read_subchannel(2, 24), data_in=24))
        except ScsiError:
            return None

    def read_isrc(self, track: int) -> str | None:
        try:
            return mmc.parse_isrc(
                self.transport.execute(mmc.cdb_read_subchannel(3, 24, track), data_in=24)
            )
        except ScsiError:
            return None

    def read_disc_info(self, *, isrc: bool = True, cdtext: bool = True) -> DiscInfo:
        toc = self.read_toc()
        info = DiscInfo(toc=toc, mcn=self.read_mcn())
        if isrc:
            for track in toc.audio_tracks:
                code = self.read_isrc(track.number)
                if code:
                    info.isrcs[track.number] = code
        if cdtext:
            info.cdtext = self.read_cdtext()
        return info

    # ------------------------------------------------------------ audio reads

    @staticmethod
    def stride(*, c2: bool, subq: bool) -> int:
        return SECTOR_SIZE + (C2_SIZE if c2 else 0) + (SUBQ_SIZE if subq else 0)

    def read_cd(
        self,
        lba: int,
        count: int,
        *,
        c2: bool = False,
        subq: bool = False,
        timeout_s: float = 30.0,
    ) -> SectorRead:
        """One READ CD command. Sector layout: audio, then C2 pointers and Q in the
        order the drive uses (see :attr:`q_before_c2`)."""
        stride = self.stride(c2=c2, subq=subq)
        cdb = mmc.cdb_read_cd(
            lba,
            count,
            error_field=mmc.ErrorField.C2 if c2 else mmc.ErrorField.NONE,
            subchannel=mmc.SubChannel.Q if subq else mmc.SubChannel.NONE,
        )
        data = self.transport.execute(cdb, data_in=stride * count, timeout_s=timeout_s)
        if len(data) != stride * count:
            raise TransportError(
                f"short read at LBA {lba}: expected {stride * count} bytes, got {len(data)}"
            )
        if stride == SECTOR_SIZE:
            return SectorRead(lba, count, data)
        q_first = False
        if c2 and subq:
            if self.q_before_c2 is None:
                self.q_before_c2 = _detect_q_before_c2(data, lba, count, stride)
            q_first = bool(self.q_before_c2)
        audio = bytearray()
        c2_buf = bytearray() if c2 else None
        qs: list[bytes] | None = [] if subq else None
        for i in range(count):
            base = i * stride
            audio += data[base : base + SECTOR_SIZE]
            pos = base + SECTOR_SIZE
            if qs is not None and q_first:
                qs.append(bytes(data[pos : pos + SUBQ_SIZE]))
                pos += SUBQ_SIZE
            if c2_buf is not None:
                c2_buf += data[pos : pos + C2_SIZE]
                pos += C2_SIZE
            if qs is not None and not q_first:
                qs.append(bytes(data[pos : pos + SUBQ_SIZE]))
        return SectorRead(
            lba,
            count,
            bytes(audio),
            bytes(c2_buf) if c2_buf is not None else None,
            tuple(qs) if qs is not None else None,
        )

    def effective_batch(self, *, c2: bool = False, subq: bool = False) -> int:
        batch = self.max_batch
        if self.max_transfer_bytes:
            batch = min(batch, max(1, self.max_transfer_bytes // self.stride(c2=c2, subq=subq)))
        return max(1, batch)

    def iter_read(
        self, start: int, count: int, *, c2: bool = False, subq: bool = False
    ) -> Iterator[SectorRead]:
        """Read ``count`` sectors in batches, shrinking the batch size if the drive or
        host rejects the transfer length. Medium errors propagate to the caller."""
        lba, end = start, start + count
        while lba < end:
            n = min(self.effective_batch(c2=c2, subq=subq), end - lba)
            try:
                result = self.read_cd(lba, n, c2=c2, subq=subq)
            except (IllegalRequestError, TransportError) as exc:
                lba_out_of_range = (
                    isinstance(exc, IllegalRequestError)
                    and exc.sense is not None
                    and exc.sense.asc == 0x21
                )
                if n == 1 or lba_out_of_range:
                    raise
                self.max_batch = max(1, n // 2)
                continue
            yield result
            lba += n

    # ------------------------------------------------------------ control

    def flush_cache(self, lba: int) -> bool:
        """Ask the drive to drop its cached copy of ``lba`` (READ(12) with FUA, no data).

        Returns False if the drive rejects the command. Whether it actually *works* is
        measured by the feature-detection wizard (Phase 3).
        """
        try:
            self.transport.execute(mmc.cdb_read12_fua(lba, 0))
        except ScsiError:
            return False
        return True

    def set_speed(self, multiplier: float | None) -> bool:
        """Set read speed (``None`` = max). False if not permitted or not supported."""
        if not self.transport.writable:
            return False
        try:
            self.transport.execute(mmc.cdb_set_cd_speed(mmc.speed_to_kbps(multiplier)))
        except (IllegalRequestError, DriveAccessError):
            return False
        return True

    def lock(self, locked: bool) -> bool:
        if not self.transport.writable:
            return False
        try:
            self.transport.execute(mmc.cdb_prevent_allow(locked))
        except (ScsiError, DriveAccessError):
            return False
        return True

    def eject(self) -> None:
        self.lock(False)
        self.transport.execute(mmc.cdb_start_stop(load_eject=True, start=False))

    def load(self) -> None:
        self.transport.execute(mmc.cdb_start_stop(load_eject=True, start=True))
