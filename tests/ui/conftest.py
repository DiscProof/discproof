from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(pred: Callable[[], bool], timeout: float = 20.0) -> None:
    from PySide6.QtCore import QCoreApplication, QEventLoop

    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting for the UI backend")
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.005)
