"""Disc insertion / removal and drive hot-plug notifications from udev."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class DiscWatcher:
    """Calls ``on_event(action, device_node)`` from udev's thread for optical drives:
    ``add``/``remove`` (drive plugged in/out) and ``change`` (medium inserted/ejected)."""

    def __init__(self, on_event: Callable[[str, str], None]) -> None:
        self._on_event = on_event
        self._observer: Any = None

    def start(self) -> bool:
        try:
            import pyudev  # noqa: PLC0415

            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by("block")
            self._observer = pyudev.MonitorObserver(monitor, callback=self._event, name="udev")
            self._observer.start()
        except Exception as exc:  # no udev (containers, tests): just no auto-detection
            log.info("disc insertion detection unavailable: %s", exc)
            return False
        return True

    def _event(self, device: Any) -> None:
        if device.properties.get("ID_CDROM") != "1" or not device.device_node:
            return
        action = device.action or ""
        if action in ("add", "remove", "change"):
            self._on_event(action, device.device_node)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.send_stop()
            self._observer = None
