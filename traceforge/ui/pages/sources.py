"""Sources page."""

from __future__ import annotations

from PySide6 import QtWidgets

from traceforge.ui.session import Session


class SourcesPage(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)
        top = QtWidgets.QHBoxLayout()
        self._add_btn = QtWidgets.QPushButton("Add source…")
        self._add_btn.clicked.connect(self._on_add)
        top.addWidget(self._add_btn)
        self._remove_btn = QtWidgets.QPushButton("Remove")
        self._remove_btn.clicked.connect(self._on_remove)
        top.addWidget(self._remove_btn)
        self._watch_btn = QtWidgets.QPushButton("Watch (live tail)")
        self._watch_btn.setCheckable(True)
        self._watch_btn.toggled.connect(self._on_watch_toggle)
        top.addWidget(self._watch_btn)
        top.addStretch(1)
        v.addLayout(top)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(
            ["Path", "Alias", "Parser", "Size (bytes)", "Events", "Unstructured", "Rejected", "Last ingested"]
        )
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        v.addWidget(self._tree, 1)

    def set_session(self, session: Session | None) -> None:
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        self._tree.clear()
        if self._session is None or not self._session.is_open():
            return
        for st in self._session.source_statuses():
            item = QtWidgets.QTreeWidgetItem(
                [
                    st.path,
                    st.alias,
                    st.parser,
                    str(st.bytes_total),
                    str(st.total_events),
                    str(st.unstructured_lines),
                    str(st.rejected_lines),
                    st.last_event_at.strftime("%Y-%m-%d %H:%M:%S") if st.last_event_at else "-",
                ]
            )
            self._tree.addTopLevelItem(item)
        for c in range(self._tree.columnCount()):
            self._tree.resizeColumnToContents(c)

    def _on_add(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Open log files", "", "Logs (*.log *.txt *.jsonl *.ndjson *.json *.csv);;All files (*)"
        )
        if not files or self._session is None:
            return
        for f in files:
            try:
                self._session.ingest_source(f)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Ingest failed", f"{f}\n\n{e}")
        self.refresh()

    def _on_remove(self) -> None:
        if self._session is None:
            return
        items = self._tree.selectedItems()
        if not items:
            return
        for it in items:
            self._session.remove_source(it.text(0))
        self.refresh()

    def _on_watch_toggle(self, on: bool) -> None:
        if self._session is None:
            return
        # The tailer is owned by the main window; this page only signals intent.
        if on:
            self._session.subscribe(lambda ev, payload: self.refresh() if ev == "source-ingested" else None)
        # Actual tailer is started by MainWindow based on this signal.
        self._watch_btn.setText("Stop watching" if on else "Watch (live tail)")
