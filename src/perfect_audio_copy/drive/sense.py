"""SCSI sense data decoding and the exception hierarchy of the drive layer."""

from __future__ import annotations

from dataclasses import dataclass

SENSE_KEYS = {
    0x0: "NO SENSE",
    0x1: "RECOVERED ERROR",
    0x2: "NOT READY",
    0x3: "MEDIUM ERROR",
    0x4: "HARDWARE ERROR",
    0x5: "ILLEGAL REQUEST",
    0x6: "UNIT ATTENTION",
    0x7: "DATA PROTECT",
    0x8: "BLANK CHECK",
    0xB: "ABORTED COMMAND",
}

# The ASC/ASCQ combinations an optical drive realistically returns while ripping.
ASC_DESCRIPTIONS: dict[tuple[int, int | None], str] = {
    (0x04, 0x01): "logical unit is becoming ready",
    (0x04, None): "logical unit not ready",
    (0x06, None): "no reference position found",
    (0x11, 0x05): "L-EC uncorrectable error",
    (0x11, 0x06): "CIRC unrecovered error",
    (0x11, None): "unrecovered read error",
    (0x15, None): "positioning error",
    (0x1A, None): "parameter list length error",
    (0x20, None): "invalid command operation code",
    (0x21, None): "logical block address out of range",
    (0x24, None): "invalid field in CDB",
    (0x26, None): "invalid field in parameter list",
    (0x28, None): "medium may have changed",
    (0x29, None): "power on, reset or bus device reset occurred",
    (0x30, None): "incompatible medium installed",
    (0x3A, None): "medium not present",
    (0x57, None): "unable to recover table of contents",
    (0x63, None): "end of user area encountered on this track",
    (0x64, None): "illegal mode for this track",
}


@dataclass(frozen=True, slots=True)
class Sense:
    key: int
    asc: int
    ascq: int

    @property
    def key_name(self) -> str:
        return SENSE_KEYS.get(self.key, f"SENSE KEY 0x{self.key:x}")

    @property
    def description(self) -> str:
        text = ASC_DESCRIPTIONS.get((self.asc, self.ascq)) or ASC_DESCRIPTIONS.get((self.asc, None))
        return text or "unknown condition"

    def __str__(self) -> str:
        return f"{self.key_name}, ASC/ASCQ {self.asc:02X}/{self.ascq:02X} ({self.description})"


def parse_sense(data: bytes) -> Sense | None:
    """Decode fixed (0x70/0x71) or descriptor (0x72/0x73) format sense data."""
    if len(data) < 4:
        return None
    response_code = data[0] & 0x7F
    if response_code in (0x70, 0x71):
        if len(data) < 14:
            return Sense(key=data[2] & 0x0F, asc=0, ascq=0) if len(data) >= 3 else None
        return Sense(key=data[2] & 0x0F, asc=data[12], ascq=data[13])
    if response_code in (0x72, 0x73):
        return Sense(key=data[1] & 0x0F, asc=data[2], ascq=data[3])
    return None


# ---------------------------------------------------------------- exceptions


class DriveError(Exception):
    """Base class for every error raised by the drive layer."""


class DriveAccessError(DriveError):
    """The device node cannot be opened (missing, permissions, not an MMC device)."""


class TransportError(DriveError):
    """The command did not complete at the host/driver level (no usable SCSI status)."""


class ScsiError(DriveError):
    """The drive answered with CHECK CONDITION."""

    def __init__(self, opcode: int, sense: Sense | None, status: int = 0x02) -> None:
        self.opcode = opcode
        self.sense = sense
        self.status = status
        detail = str(sense) if sense else f"SCSI status 0x{status:02x}, no sense data"
        super().__init__(f"command 0x{opcode:02X} failed: {detail}")


class NotReadyError(ScsiError):
    pass


class MediumNotPresentError(NotReadyError):
    pass


class MediumError(ScsiError):
    """Unrecoverable read error at the medium level — the engine handles these."""


class HardwareError(ScsiError):
    pass


class IllegalRequestError(ScsiError):
    """Command, field or LBA not supported/valid for this drive or disc."""


class UnitAttentionError(ScsiError):
    """Media changed, reset, etc. Usually worth one retry."""


def error_for(opcode: int, sense: Sense | None, status: int = 0x02) -> ScsiError:
    """Pick the most specific exception class for a CHECK CONDITION."""
    if sense is None:
        return ScsiError(opcode, sense, status)
    match sense.key:
        case 0x2 if sense.asc == 0x3A:
            return MediumNotPresentError(opcode, sense, status)
        case 0x2:
            return NotReadyError(opcode, sense, status)
        case 0x3:
            return MediumError(opcode, sense, status)
        case 0x4:
            return HardwareError(opcode, sense, status)
        case 0x5:
            return IllegalRequestError(opcode, sense, status)
        case 0x6:
            return UnitAttentionError(opcode, sense, status)
        case _:
            return ScsiError(opcode, sense, status)
