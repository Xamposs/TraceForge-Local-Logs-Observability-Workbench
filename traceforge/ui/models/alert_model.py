"""Qt model for the alerts page."""

from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui

from traceforge.models.alerts import Alert
from traceforge.ui.style import SEVERITY_COLORS


class AlertTableModel(QtCore.QAbstractTableModel):
    COLUMNS = ("fired_at", "severity", "rule_name", "title", "threshold", "observed", "id")

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._alerts: list[Alert] = []

    def set_alerts(self, alerts: list[Alert]) -> None:
        self.beginResetModel()
        self._alerts = list(alerts)
        self.endResetModel()

    def alerts(self) -> list[Alert]:
        return list(self._alerts)

    def acknowledge(self, index: int) -> None:
        if 0 <= index < len(self._alerts):
            self._alerts[index].acknowledged = True
            self.dataChanged.emit(
                self.index(index, 0),
                self.index(index, self.columnCount() - 1),
            )

    def dismiss(self, index: int) -> None:
        if 0 <= index < len(self._alerts):
            self._alerts[index].dismissed = True
            self.dataChanged.emit(
                self.index(index, 0),
                self.index(index, self.columnCount() - 1),
            )

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._alerts)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(
        self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != QtCore.Qt.Orientation.Horizontal:
            return None
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        headers = {
            "fired_at": "Fired At",
            "severity": "Severity",
            "rule_name": "Rule",
            "title": "Title",
            "threshold": "Threshold",
            "observed": "Observed",
            "id": "ID",
        }
        return headers.get(self.COLUMNS[section], self.COLUMNS[section])

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        col = self.COLUMNS[index.column()]
        alert = self._alerts[index.row()]
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole):
            if col == "fired_at":
                return alert.fired_at.strftime("%Y-%m-%d %H:%M:%S")
            if col == "severity":
                return alert.severity
            if col == "rule_name":
                return alert.rule_name
            if col == "title":
                return alert.title
            if col == "threshold":
                return alert.threshold
            if col == "observed":
                return alert.observed
            if col == "id":
                return alert.id
        if role == QtCore.Qt.ItemDataRole.ForegroundRole and col == "severity":
            return QtGui.QColor(SEVERITY_COLORS.get(alert.severity, SEVERITY_COLORS["UNKNOWN"]))
        return None
