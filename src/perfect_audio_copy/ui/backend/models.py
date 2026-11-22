"""The track table model."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt


@dataclass(slots=True)
class TrackRow:
    number: int
    title: str
    artist: str
    length: str  # m:ss
    pregap: str = "—"
    selected: bool = True
    status: str = "pending"
    crc: str = ""
    confidence: int = 0
    progress: float = 0.0
    map_version: int = 0
    data_track: bool = False


ROLES = {Qt.ItemDataRole.UserRole + 1 + i: f.name for i, f in enumerate(fields(TrackRow))}
_BY_NAME = {name: role for role, name in ROLES.items()}
# QML names in camelCase
_QML = {
    role: "".join(p.title() if i else p for i, p in enumerate(n.split("_")))
    for role, n in ROLES.items()
}


class TrackModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[TrackRow] = []

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {r: QByteArray(n.encode()) for r, n in _QML.items()}

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        name = ROLES.get(role)
        return getattr(self.rows[index.row()], name) if name else None

    def reset(self, rows: list[TrackRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def row_of(self, number: int) -> int:
        for i, r in enumerate(self.rows):
            if r.number == number:
                return i
        return -1

    def update(self, number: int, **changes: Any) -> None:
        i = self.row_of(number)
        if i < 0:
            return
        row = self.rows[i]
        roles = []
        for k, v in changes.items():
            if getattr(row, k) != v:
                setattr(row, k, v)
                roles.append(_BY_NAME[k])
        if roles:
            idx = self.index(i)
            self.dataChanged.emit(idx, idx, roles)

    def selected_numbers(self) -> list[int]:
        return [r.number for r in self.rows if r.selected and not r.data_track]
