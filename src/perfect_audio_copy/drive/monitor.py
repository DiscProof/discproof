"""Disc insertion/removal and drive hot-plug notifications via udev.

udev's cdrom_id sets ``ID_CDROM_MEDIA*`` properties on the ``sr`` block device and emits
a ``change`` event when media is inserted or ejected.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class DiscEventKind(enum.Enum):
    DRIVE_ADDED = "drive-added"
    DRIVE_REMOVED = "drive-removed"
    AUDIO_DISC_INSERTED = "audio-disc-inserted"
    OTHER_DISC_INSERTED = "other-disc-inserted"
    DISC_REMOVED = "disc-removed"


@dataclass(frozen=True, slots=True)
class DiscEvent:
    kind: DiscEventKind
    path: str  # /dev/sr0
    audio_tracks: int = 0


def classify_event(action: str, props: Mapping[str, str]) -> DiscEvent | None:
    """Turn a udev event on a block device into a :class:`DiscEvent` (pure function)."""
    devname = props.get("DEVNAME", "")
    if not devname or props.get("ID_CDROM") != "1":
        return None
    if action == "add":
        return DiscEvent(DiscEventKind.DRIVE_ADDED, devname)
    if action == "remove":
        return DiscEvent(DiscEventKind.DRIVE_REMOVED, devname)
    if action != "change":
        return None
    if props.get("ID_CDROM_MEDIA") != "1":
        return DiscEvent(DiscEventKind.DISC_REMOVED, devname)
    audio = props.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO", "0")
    count = int(audio) if audio.isdigit() else 0
    if count > 0:
        return DiscEvent(DiscEventKind.AUDIO_DISC_INSERTED, devname, count)
    return DiscEvent(DiscEventKind.OTHER_DISC_INSERTED, devname)


class DiscMonitor:
    """Calls ``callback`` from a background thread for every :class:`DiscEvent`.

    The UI layer must marshal the callback onto the Qt main thread (queued signal).
    """

    def __init__(self, callback: Callable[[DiscEvent], None]) -> None:
        import pyudev  # noqa: PLC0415 - optional at import time (tests, non-Linux)

        self._callback = callback
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="block", device_type="disk")
        self._observer: Any = pyudev.MonitorObserver(monitor, callback=self._on_device)

    def _on_device(self, device: Any) -> None:
        event = classify_event(str(device.action), dict(device.properties))
        if event is not None:
            self._callback(event)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
