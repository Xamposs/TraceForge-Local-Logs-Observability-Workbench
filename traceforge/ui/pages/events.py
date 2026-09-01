"""Events page — virtualized table backed by a QueryResult."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from traceforge.query import QueryResult
from traceforge.ui.models.event_model import EventTableModel


class EventsPage(QtWidgets.QWidget):
    run_query_requested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Splitter: table on the left, details on the right.
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Table.
        self._table = QtWidgets.QTableView()
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._model = EventTableModel(self)
        self._table.setModel(self._model)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        # Details.
        details = QtWidgets.QFrame()
        details.setObjectName("detailsPanel")
        v = QtWidgets.QVBoxLayout(details)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(6)
        title = QtWidgets.QLabel("Event details")
        title.setStyleSheet("font-weight:600; color:#aab0b8;")
        v.addWidget(title)
        self._details = QtWidgets.QTextEdit()
        self._details.setReadOnly(True)
        v.addWidget(self._details, 1)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self._splitter = splitter

        # Status / metadata.
        self._status = QtWidgets.QLabel("No results yet.")
        self._status.setStyleSheet("color:#6c727b; padding:6px 12px;")

    def status_label(self) -> QtWidgets.QLabel:
        return self._status

    def set_result(self, result: QueryResult) -> None:
        self._model.set_result(result)
        self._table.resizeColumnsToContents()
        if result.is_aggregation:
            self._status.setText(f"Aggregation: {result.row_count} group(s) in {result.elapsed_ms:.1f} ms")
        else:
            truncated = " (truncated)" if result.truncated else ""
            self._status.setText(f"{result.row_count} events{truncated} in {result.elapsed_ms:.1f} ms")
        self._details.clear()

    def clear(self) -> None:
        self._model.set_result(None)
        self._status.setText("No results yet.")
        self._details.clear()

    def selected_event_id(self) -> str | None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        return self._model.event_id_at(idx.row())

    def _on_selection_changed(self) -> None:
        row = self._table.currentIndex().row()
        rec = self._model.row_at(row)
        if rec is None:
            self._details.clear()
            return
        cols = self._model.result().columns if self._model.result() else []
        body = []
        body.append("<style>td{color:#aab0b8; padding:2px 8px;} td.k{color:#6c727b;}</style>")
        body.append("<table>")
        for c, v in zip(cols, rec, strict=False):
            v_s = "" if v is None else str(v)
            v_s = v_s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body.append(f"<tr><td class='k'>{c}</td><td>{v_s}</td></tr>")
        body.append("</table>")
        self._details.setHtml("".join(body))
