"""Album cover and per-track sector-map images for QML."""

from __future__ import annotations

import threading

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider

from discproof.engine.events import SectorStatus
from discproof.ui.backend.theme import TOKENS

PENDING, OK, RECOVERED, SUSPICIOUS = 0, 1, 2, 3
STATUS_CODE = {
    SectorStatus.OK: OK,
    SectorStatus.FILLED: OK,
    SectorStatus.RECOVERED: RECOVERED,
    SectorStatus.SUSPICIOUS: SUSPICIOUS,
}
COLORS = {
    PENDING: TOKENS["surface3"],
    OK: TOKENS["positive"],
    RECOVERED: TOKENS["warning"],
    SUSPICIOUS: TOKENS["negative"],
}


class CoverProvider(QQuickImageProvider):
    """``image://cover/<serial>``: the current cover (serial busts QML's cache)."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._image = QImage()
        self._lock = threading.Lock()

    def set_jpeg(self, data: bytes | None) -> None:
        img = QImage()
        if data:
            img.loadFromData(data)
        with self._lock:
            self._image = img

    def requestImage(self, id_: str, size: QSize, requested: QSize) -> QImage:
        with self._lock:
            img = self._image
        if img.isNull():
            img = QImage(1, 1, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
        elif requested.width() > 0 and requested.height() > 0:
            img = img.scaled(
                requested,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        size.setWidth(img.width())
        size.setHeight(img.height())
        return img


class SectorMaps:
    """Per-track sector status (pending / ok / recovered / suspicious), written by the
    rip worker and drawn as thin strips. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._maps: dict[int, npt.NDArray[np.uint8]] = {}
        self._starts: list[tuple[int, int, int]] = []  # (start, end, track)

    def reset(self, spans: list[tuple[int, int, int]]) -> None:
        """spans: (track, first LBA, end LBA)."""
        with self._lock:
            self._maps = {n: np.zeros(max(1, e - s), dtype=np.uint8) for n, s, e in spans}
            self._starts = sorted((s, e, n) for n, s, e in spans)

    def clear_track(self, number: int) -> None:
        """A new pass over a track (Test & Copy) starts from pending again."""
        with self._lock:
            m = self._maps.get(number)
            if m is not None:
                m[:] = PENDING

    def mark(self, lba: int, statuses: list[SectorStatus]) -> set[int]:
        """Record final statuses of drive sectors from ``lba``; returns touched tracks.
        A sector keeps its worst status across passes."""
        touched: set[int] = set()
        with self._lock:
            for i, st in enumerate(statuses):
                code = STATUS_CODE[st]
                x = lba + i
                for s, e, n in self._starts:
                    if s <= x < e:
                        m = self._maps[n]
                        m[x - s] = max(m[x - s], code)
                        touched.add(n)
                        break
        return touched

    def counts(self, number: int) -> tuple[int, int, int, int]:
        with self._lock:
            m = self._maps.get(number)
            if m is None:
                return 0, 0, 0, 0
            c = np.bincount(m, minlength=4)
            return int(c[0]), int(c[1]), int(c[2]), int(c[3])

    def render(self, number: int, width: int, height: int) -> QImage:
        """Each pixel column shows the worst status of the sectors it covers, so a
        single bad sector stays visible on a long track."""
        width, height = max(1, width), max(1, height)
        with self._lock:
            m = self._maps.get(number)
            cols = np.zeros(width, dtype=np.uint8)
            if m is not None and len(m):
                edges = np.linspace(0, len(m), width + 1).astype(int)
                for x in range(width):
                    a, b = edges[x], max(edges[x + 1], edges[x] + 1)
                    cols[x] = m[a:b].max() if a < len(m) else 0
        # Widen recovered/suspicious marks to >= 3 px so one bad sector on a long
        # track is still visible; never hides a worse status under a better one.
        marks = np.where(cols >= RECOVERED, cols, 0)
        wide = np.maximum(marks, np.maximum(np.roll(marks, 1), np.roll(marks, -1)))
        if width > 2:
            wide[0] = max(marks[0], marks[1])
            wide[-1] = max(marks[-1], marks[-2])
        cols = np.where(wide > cols, wide, cols).astype(np.uint8)
        img = QImage(width, height, QImage.Format.Format_RGB32)
        p = QPainter(img)
        start = 0
        for x in range(1, width + 1):
            if x == width or cols[x] != cols[start]:
                p.fillRect(start, 0, x - start, height, QColor(COLORS[int(cols[start])]))
                start = x
        p.end()
        return img


class SectorMapProvider(QQuickImageProvider):
    """``image://sectormap/<track>/<version>``."""

    def __init__(self, maps: SectorMaps) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.maps = maps

    def requestImage(self, id_: str, size: QSize, requested: QSize) -> QImage:
        try:
            number = int(id_.split("/", maxsplit=1)[0])
        except ValueError:
            number = -1
        w = requested.width() if requested.width() > 0 else 600
        h = requested.height() if requested.height() > 0 else 6
        img = self.maps.render(number, w, h)
        size.setWidth(w)
        size.setHeight(h)
        return img
