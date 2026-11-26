"""Desktop notifications via the freedesktop notification service (shown natively by
Plasma). Uses ``gdbus`` (glib2, always present on Fedora), which reads the method's
signature and so sends ``replaces_id`` as the required uint32; falls back to
``notify-send``. Does nothing if neither is available."""

from __future__ import annotations

import logging
import shutil
import subprocess

from discproof import APP_ID, APP_NAME

log = logging.getLogger(__name__)


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def notify(summary: str, body: str, *, icon: str = APP_ID, timeout_ms: int = 8000) -> bool:
    gdbus = shutil.which("gdbus")
    try:
        if gdbus:
            cmd = [
                gdbus, "call", "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify",
                _gvariant_str(APP_NAME), "0", _gvariant_str(icon),
                _gvariant_str(summary), _gvariant_str(body), "[]",
                "{'desktop-entry': <" + _gvariant_str(APP_ID) + ">}", str(timeout_ms),
            ]  # fmt: skip
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "-a", APP_NAME, "-i", icon, summary, body]
        else:
            return False
        proc = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("notification failed: %s", exc)
        return False
    if proc.returncode != 0:
        log.info("notification failed: %s", proc.stderr.decode("utf-8", "replace").strip())
    return proc.returncode == 0
