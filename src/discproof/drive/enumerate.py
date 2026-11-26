"""Find optical drives via sysfs (no root, no hardware access needed)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DriveInfo:
    path: str  # /dev/sr0
    kernel_name: str  # sr0
    vendor: str
    model: str
    revision: str
    max_transfer_bytes: int | None
    readable: bool
    writable: bool

    @property
    def name(self) -> str:
        return f"{self.vendor} {self.model}".strip() or self.kernel_name

    @property
    def accessible(self) -> bool:
        return self.readable


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


def max_transfer_bytes(dev_path: str, sysfs_root: str = "/sys") -> int | None:
    """Largest single transfer the block layer allows for this device (max_sectors_kb)."""
    name = os.path.basename(os.path.realpath(dev_path))
    kb = _read(Path(sysfs_root, "class", "block", name, "queue", "max_sectors_kb"))
    return int(kb) * 1024 if kb.isdigit() and int(kb) > 0 else None


def _natural_key(name: str) -> tuple[str, int]:
    m = re.match(r"([a-z]+)(\d+)$", name)
    return (m.group(1), int(m.group(2))) if m else (name, 0)


def list_drives(sysfs_root: str = "/sys", dev_root: str = "/dev") -> list[DriveInfo]:
    """All SCSI/ATAPI/USB optical drives (``sr*`` block devices), in kernel order."""
    block = Path(sysfs_root, "class", "block")
    try:
        names = [p.name for p in block.iterdir() if re.fullmatch(r"sr\d+", p.name)]
    except OSError:
        return []
    drives = []
    for name in sorted(names, key=_natural_key):
        dev = os.path.join(dev_root, name)
        device_dir = block / name / "device"
        drives.append(
            DriveInfo(
                path=dev,
                kernel_name=name,
                vendor=_read(device_dir / "vendor"),
                model=_read(device_dir / "model"),
                revision=_read(device_dir / "rev"),
                max_transfer_bytes=max_transfer_bytes(dev, sysfs_root),
                readable=os.access(dev, os.R_OK),
                writable=os.access(dev, os.W_OK),
            )
        )
    return drives
