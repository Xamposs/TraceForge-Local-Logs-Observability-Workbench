"""Alerts page."""

from __future__ import annotations

from PySide6 import QtWidgets

from traceforge.models.alerts import Alert
from traceforge.ui.models.alert_model import AlertTableModel
from traceforge.ui.session import Session


class AlertsPage(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)
        top = QtWidgets.QHBoxLayout()
        self._evaluate_btn = QtWidgets.QPushButton("Evaluate now")
        self._evaluate_btn.clicked.connect(self.refresh)
        top.addWidget(self._evaluate_btn)
        top.addStretch(1)
        v.addLayout(top)

        self._table = QtWidgets.QTableView()
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self._model = AlertTableModel(self)
        self._table.setModel(self._model)
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        v.addWidget(self._table, 1)
        self._details = QtWidgets.QTextEdit()
        self._details.setReadOnly(True)
        v.addWidget(self._details, 1)
        button_row = QtWidgets.QHBoxLayout()
        self._ack_btn = QtWidgets.QPushButton("Acknowledge")
        self._ack_btn.clicked.connect(self._acknowledge)
        self._dismiss_btn = QtWidgets.QPushButton("Dismiss")
        self._dismiss_btn.clicked.connect(self._dismiss)
        button_row.addStretch(1)
        button_row.addWidget(self._ack_btn)
        button_row.addWidget(self._dismiss_btn)
        v.addLayout(button_row)

    def set_session(self, session: Session | None) -> None:
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        if self._session is None or not self._session.is_open():
            return
        alerts = self._session.evaluate_rules()
        self._model.set_alerts(alerts)
        self._table.resizeColumnsToContents()
        self._details.clear()

    def _on_selection(self) -> None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return
        alerts = self._model.alerts()
        if not (0 <= idx.row() < len(alerts)):
            return
        a: Alert = alerts[idx.row()]
        body = (
            f"<style>td.k{{color:#6c727b; padding:2px 8px;}}td{{padding:2px 8px;}}</style>"
            f"<table>"
            f"<tr><td class='k'>Rule</td><td>{a.rule_name}</td></tr>"
            f"<tr><td class='k'>Severity</td><td>{a.severity}</td></tr>"
            f"<tr><td class='k'>Fired at</td><td>{a.fired_at.isoformat() if a.fired_at else '-'}</td></tr>"
            f"<tr><td class='k'>Title</td><td>{a.title}</td></tr>"
            f"<tr><td class='k'>Threshold</td><td>{a.threshold or '-'}</td></tr>"
            f"<tr><td class='k'>Observed</td><td>{a.observed or '-'}</td></tr>"
            f"<tr><td class='k'>Time window</td><td>{a.time_window or '-'}</td></tr>"
            f"</table>"
            f"<div style='margin-top:8px; color:#aab0b8;'>{a.explanation}</div>"
        )
        self._details.setHtml(body)

    def _acknowledge(self) -> None:
        idx = self._table.currentIndex()
        if idx.isValid():
            self._model.acknowledge(idx.row())

    def _dismiss(self) -> None:
        idx = self._table.currentIndex()
        if idx.isValid():
            self._model.dismiss(idx.row())
