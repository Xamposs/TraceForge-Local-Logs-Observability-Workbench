"""Correlation page."""

from __future__ import annotations

from PySide6 import QtWidgets

from traceforge.analytics.correlation import build_hierarchy, collect_correlation
from traceforge.ui.session import Session


class CorrelationPage(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Trace ID:"))
        self._trace = QtWidgets.QLineEdit()
        controls.addWidget(self._trace)
        controls.addWidget(QtWidgets.QLabel("Request ID:"))
        self._request = QtWidgets.QLineEdit()
        controls.addWidget(self._request)
        controls.addWidget(QtWidgets.QLabel("Session ID:"))
        self._session_id = QtWidgets.QLineEdit()
        controls.addWidget(self._session_id)
        self._run_btn = QtWidgets.QPushButton("Show")
        self._run_btn.clicked.connect(self._on_run)
        controls.addWidget(self._run_btn)
        v.addLayout(controls)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(["Service", "When", "Level", "Message", "Span / Parent", "Duration (ms)"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        v.addWidget(self._tree, 1)

    def set_session(self, session: Session | None) -> None:
        self._session = session

    def prefill(
        self, *, trace_id: str | None = None, request_id: str | None = None, session_id: str | None = None
    ) -> None:
        if trace_id is not None:
            self._trace.setText(trace_id)
        if request_id is not None:
            self._request.setText(request_id)
        if session_id is not None:
            self._session_id.setText(session_id)
        self._on_run()

    def _on_run(self) -> None:
        if self._session is None or not self._session.is_open():
            return
        t = self._trace.text().strip() or None
        r = self._request.text().strip() or None
        s = self._session_id.text().strip() or None
        if not (t or r or s):
            return
        events = collect_correlation(self._session.db, trace_id=t, request_id=r, session_id=s)
        groups = build_hierarchy(events)
        self._tree.clear()
        for group in groups:
            header_text = ", ".join(e.service or "-" for e in group) if group else "no events"
            top = QtWidgets.QTreeWidgetItem([header_text, "", "", "", "", ""])
            self._tree.addTopLevelItem(top)
            for e in group:
                when = e.timestamp.strftime("%H:%M:%S") if e.timestamp else "-"
                sp = e.span_id or ""
                if e.parent_span_id:
                    sp += f" ← {e.parent_span_id}"
                msg = (e.message or "").replace("\n", " ")[:80]
                item = QtWidgets.QTreeWidgetItem(
                    [
                        e.service or "-",
                        when,
                        e.severity,
                        msg,
                        sp,
                        f"{e.duration_ms:.1f}" if e.duration_ms is not None else "",
                    ]
                )
                top.addChild(item)
            top.setExpanded(True)
        for c in range(self._tree.columnCount()):
            self._tree.resizeColumnToContents(c)
