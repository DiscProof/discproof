"""SCSI command transport.

:class:`BaseTransport` holds the status/sense handling and retry policy and is what the
rest of the drive layer talks to. :class:`SgioTransport` implements it with the Linux
``SG_IO`` ioctl on a ``/dev/sr*`` block device, in pure Python via :mod:`ctypes`.

Permissions note: as an unprivileged user the kernel only allows "read-class" MMC
commands (READ CD, READ TOC, INQUIRY, ...) on a read-only fd; "write-class" commands
such as SET CD SPEED and PREVENT/ALLOW MEDIUM REMOVAL need the device opened O_RDWR.
Fedora's logind ``uaccess`` ACL grants the desktop user read/write on optical drives.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from discproof.drive.sense import (
    DriveAccessError,
    NotReadyError,
    ScsiError,
    TransportError,
    UnitAttentionError,
    error_for,
    parse_sense,
)

# --------------------------------------------------------------- SG_IO ABI

SG_IO = 0x2285
SG_GET_VERSION_NUM = 0x2282
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3

STATUS_GOOD = 0x00
STATUS_CHECK_CONDITION = 0x02
STATUS_BUSY = 0x08
DRIVER_SENSE = 0x08

SENSE_BUFFER_LEN = 64
DEFAULT_TIMEOUT_S = 30.0


class SgIoHdr(ctypes.Structure):
    """``struct sg_io_hdr`` from <scsi/sg.h>."""

    _fields_ = (
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    )


# ----------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class RawResult:
    """What came back from one command submission, before interpretation."""

    status: int
    data: bytes = b""
    sense: bytes = b""
    host_status: int = 0
    driver_status: int = 0
    duration_ms: int = 0


class BaseTransport(ABC):
    """Executes CDBs, turns CHECK CONDITION into typed exceptions, retries transient states.

    Retries only UNIT ATTENTION, NOT READY/"becoming ready" and BUSY. Medium errors are
    never retried here: deciding how to re-read is the extraction engine's job.
    """

    def __init__(
        self,
        *,
        retries: int = 3,
        retry_delay_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self._sleep = sleep
        #: Duration of the last successful command, for speed statistics.
        self.last_duration_ms = 0

    @property
    def writable(self) -> bool:
        """Whether write-class commands (SET CD SPEED, lock/unlock) are permitted."""
        return True

    @abstractmethod
    def _submit(
        self, cdb: bytes, data_in: int, data_out: bytes | None, timeout_s: float
    ) -> RawResult: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release the device. Default: nothing to release."""

    def execute(
        self,
        cdb: bytes,
        *,
        data_in: int = 0,
        data_out: bytes | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> bytes:
        """Run one command; return the data-in bytes actually transferred."""
        if data_in and data_out is not None:
            raise ValueError("bidirectional transfers are not supported")
        opcode = cdb[0]
        attempt = 0
        while True:
            raw = self._submit(cdb, data_in, data_out, timeout_s)
            if raw.host_status or (raw.driver_status & ~DRIVER_SENSE):
                raise TransportError(
                    f"command 0x{opcode:02X} failed in transport: host_status=0x"
                    f"{raw.host_status:02x} driver_status=0x{raw.driver_status:02x}"
                )
            if raw.status == STATUS_GOOD:
                self.last_duration_ms = raw.duration_ms
                return raw.data

            err: ScsiError
            if raw.status == STATUS_CHECK_CONDITION:
                sense = parse_sense(raw.sense)
                if sense is not None and sense.key == 0x1:  # RECOVERED ERROR: data is good
                    self.last_duration_ms = raw.duration_ms
                    return raw.data
                err = error_for(opcode, sense, raw.status)
                retryable = isinstance(err, UnitAttentionError) or (
                    type(err) is NotReadyError and sense is not None and sense.asc == 0x04
                )
            elif raw.status == STATUS_BUSY:
                err = ScsiError(opcode, None, raw.status)
                retryable = True
            else:
                raise ScsiError(opcode, None, raw.status)

            if not retryable or attempt >= self.retries:
                raise err
            attempt += 1
            self._sleep(self.retry_delay_s)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class SgioTransport(BaseTransport):
    """SG_IO on a Linux optical block device (``/dev/sr0``)."""

    def __init__(
        self,
        path: str,
        *,
        retries: int = 3,
        retry_delay_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(retries=retries, retry_delay_s=retry_delay_s, sleep=sleep)
        self.path = path
        self._fd = -1
        self._writable = True
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise DriveAccessError(f"cannot open {path}: {exc.strerror}") from exc
            try:
                self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc2:
                raise DriveAccessError(
                    f"cannot open {path}: {exc2.strerror}. Is your user allowed to access "
                    "the drive (logged in locally, or member of the 'cdrom' group)?"
                ) from exc2
            self._writable = False
        try:
            version = ctypes.c_int(0)
            fcntl.ioctl(self._fd, SG_GET_VERSION_NUM, version)
        except OSError as exc:
            os.close(self._fd)
            raise DriveAccessError(f"{path} does not support SG_IO: {exc.strerror}") from exc
        if version.value < 30000:
            os.close(self._fd)
            raise DriveAccessError(f"{path}: SG driver version {version.value} is too old")

    @property
    def writable(self) -> bool:
        return self._writable

    def fileno(self) -> int:
        return self._fd

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def _submit(
        self, cdb: bytes, data_in: int, data_out: bytes | None, timeout_s: float
    ) -> RawResult:
        if self._fd < 0:
            raise DriveAccessError(f"{self.path} is closed")
        cdb_buf = ctypes.create_string_buffer(bytes(cdb), len(cdb))
        sense_buf = ctypes.create_string_buffer(SENSE_BUFFER_LEN)
        hdr = SgIoHdr()
        hdr.interface_id = ord("S")
        hdr.cmd_len = len(cdb)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.mx_sb_len = SENSE_BUFFER_LEN
        hdr.sbp = ctypes.addressof(sense_buf)
        hdr.timeout = max(1, int(timeout_s * 1000))

        data_buf: ctypes.Array[ctypes.c_char] | None = None
        if data_in:
            data_buf = ctypes.create_string_buffer(data_in)
            hdr.dxfer_direction = SG_DXFER_FROM_DEV
            hdr.dxfer_len = data_in
            hdr.dxferp = ctypes.addressof(data_buf)
        elif data_out is not None:
            data_buf = ctypes.create_string_buffer(bytes(data_out), len(data_out))
            hdr.dxfer_direction = SG_DXFER_TO_DEV
            hdr.dxfer_len = len(data_out)
            hdr.dxferp = ctypes.addressof(data_buf)
        else:
            hdr.dxfer_direction = SG_DXFER_NONE

        try:
            fcntl.ioctl(self._fd, SG_IO, hdr)
        except OSError as exc:
            if exc.errno in (errno.EPERM, errno.EACCES):
                raise DriveAccessError(
                    f"{self.path}: the kernel refused command 0x{cdb[0]:02X} "
                    "(device opened read-only?)"
                ) from exc
            raise TransportError(f"SG_IO ioctl failed: {exc.strerror}") from exc

        data = b""
        if data_in and data_buf is not None:
            got = max(0, data_in - max(0, hdr.resid))
            data = data_buf.raw[:got]
        return RawResult(
            status=hdr.status,
            data=data,
            sense=sense_buf.raw[: hdr.sb_len_wr],
            host_status=hdr.host_status,
            driver_status=hdr.driver_status,
            duration_ms=hdr.duration,
        )
