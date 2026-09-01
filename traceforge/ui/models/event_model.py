"""Qt table model for the events page.

We do not load every event into a QTableWidget; instead we use
QAbstractTableModel with a backing QueryResult and page through it lazily.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6 import QtCore, QtGui

from traceforge.query import QueryResult
from traceforge.ui.style import SEVERITY_COLORS

COLUMNS = (
    "timestamp",
    "severity",
    "service",
    "message",
    "source",
    "trace_id",
    "request_id",
    "duration_ms",
    "status_code",
    "logger",
    "host",
    "process",
    "thread",
    "exception_type",
    "raw_format",
    "line_number",
    "event_id",
    "source_path",
)

HEADERS = {
    "timestamp": "Timestamp",
    "severity": "Level",
    "service": "Service",
    "message": "Message",
    "source": "Source",
    "trace_id": "Trace ID",
    "request_id": "Request ID",
    "duration_ms": "Duration (ms)",
    "status_code": "Status",
    "logger": "Logger",
    "host": "Host",
    "process": "Process",
    "thread": "Thread",
    "exception_type": "Exception",
    "raw_format": "Format",
    "line_number": "Line",
    "event_id": "Event ID",
    "source_path": "Path",
}


class EventTableModel(QtCore.QAbstractTableModel):
    """Paged, read-only table model over a QueryResult."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._result: QueryResult | None = None
        self._column_index: list[str] = []

    def set_result(self, result: QueryResult | None) -> None:
        self.beginResetModel()
        self._result = result
        if result is not None:
            # Prefer the predefined ordering for row-mode results, but fall
            # back to whatever columns the query returned for aggregations.
            preferred = [c for c in COLUMNS if c in result.columns]
            others = [c for c in result.columns if c not in COLUMNS]
            self._column_index = preferred + others
        else:
            self._column_index = []
        self.endResetModel()

    def result(self) -> QueryResult | None:
        return self._result

    def row_at(self, row: int) -> tuple | None:
        if self._result is None or row < 0 or row >= len(self._result.rows):
            return None
        return self._result.rows[row]

    def event_id_at(self, row: int) -> str | None:
        r = self.row_at(row)
        if r is None:
            return None
        try:
            idx = self._result.columns.index("event_id")
            return r[idx]
        except ValueError:
            return None

    # ---- Qt overrides ----

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 0 if self._result is None else len(self._result.rows)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._column_index)

    def headerData(
        self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == QtCore.Qt.Orientation.Horizontal:
            if role == QtCore.Qt.ItemDataRole.DisplayRole and 0 <= section < len(self._column_index):
                return HEADERS.get(self._column_index[section], self._column_index[section])
            return None
        return super().headerData(section, orientation, role)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or self._result is None:
            return None
        col = self._column_index[index.column()]
        row = self._result.rows[index.row()]
        try:
            value = row[self._result.columns.index(col)]
        except (ValueError, IndexError):
            return None
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d %H:%M:%S")
            if value is None:
                return ""
            return str(value)
        if role == QtCore.Qt.ItemDataRole.ForegroundRole and col == "severity":
            color = SEVERITY_COLORS.get(str(value), SEVERITY_COLORS["UNKNOWN"])
            return QtGui.QColor(color)
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            if col == "message" and isinstance(value, str):
                return value
            if col == "source_path" and isinstance(value, str):
                return value
        return None
