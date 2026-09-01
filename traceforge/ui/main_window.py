"""TraceForge main window."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from traceforge import __version__
from traceforge.app_paths import AppPaths
from traceforge.demo import DemoConfig
from traceforge.demo import generate as generate_demo
from traceforge.ingestion.pipeline import CancellationToken
from traceforge.live.tailer import LiveTailer
from traceforge.query import QueryError
from traceforge.ui.pages.alerts import AlertsPage
from traceforge.ui.pages.correlation import CorrelationPage
from traceforge.ui.pages.errors import ErrorsPage
from traceforge.ui.pages.events import EventsPage
from traceforge.ui.pages.overview import OverviewPage
from traceforge.ui.pages.sources import SourcesPage
from traceforge.ui.pages.welcome import WelcomePage
from traceforge.ui.session import Session

PAGES = [
    "Overview",
    "Events",
    "Errors",
    "Correlation",
    "Alerts",
    "Sources",
]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TraceForge")
        self.resize(1400, 880)
        self.setObjectName("main")
        self.app_paths = AppPaths.default()
        self.session = Session(self.app_paths)
        self._tailer: LiveTailer | None = None
        self._ingest_cancels: dict[str, CancellationToken] = {}

        # Status bar.
        self._status = QtWidgets.QLabel("Ready.")
        self._status.setStyleSheet("color:#6c727b;")
        sb = QtWidgets.QStatusBar()
        sb.addWidget(self._status, 1)
        sb.addPermanentWidget(QtWidgets.QLabel(f"TraceForge {__version__}"))
        self.setStatusBar(sb)

        # Top toolbar.
        tb = QtWidgets.QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(18, 18))
        self.addToolBar(tb)
        self._action_new_ws = QtGui.QAction("New Workspace", self)
        self._action_open_ws = QtGui.QAction("Open Workspace…", self)
        self._action_save_ws = QtGui.QAction("Save", self)
        self._action_open_logs = QtGui.QAction("Open Logs…", self)
        self._action_demo = QtGui.QAction("Launch Demo", self)
        self._action_export = QtGui.QAction("Export…", self)
        for a in (
            self._action_new_ws,
            self._action_open_ws,
            self._action_save_ws,
            self._action_open_logs,
            self._action_demo,
            self._action_export,
        ):
            tb.addAction(a)
        self._action_new_ws.triggered.connect(self._on_new_workspace)
        self._action_open_ws.triggered.connect(self._on_open_workspace)
        self._action_save_ws.triggered.connect(self._on_save_workspace)
        self._action_open_logs.triggered.connect(self._on_open_logs)
        self._action_demo.triggered.connect(self._on_launch_demo)
        self._action_export.triggered.connect(self._on_export)

        # Central layout.
        central = QtWidgets.QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)

        # Sidebar.
        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sv = QtWidgets.QVBoxLayout(sidebar)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        title = QtWidgets.QLabel("TRACEFORGE")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:#aab0b8; font-weight:600; letter-spacing:0.18em; padding:18px 0;")
        sv.addWidget(title)
        self._list = QtWidgets.QListWidget()
        self._list.setObjectName("sidebar")
        for name in PAGES:
            QtWidgets.QListWidgetItem(name, self._list)
        self._list.currentRowChanged.connect(self._on_page_changed)
        sv.addWidget(self._list, 1)

        # Workspace label at bottom of sidebar.
        self._ws_label = QtWidgets.QLabel("No workspace")
        self._ws_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._ws_label.setStyleSheet("color:#6c727b; padding:10px 8px; border-top: 1px solid #2a2f36;")
        self._ws_label.setWordWrap(True)
        sv.addWidget(self._ws_label)

        hbox.addWidget(sidebar)

        # Right side: query bar + stacked pages.
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self._query_bar = self._build_query_bar()
        rv.addWidget(self._query_bar)

        self._stack = QtWidgets.QStackedWidget()
        self._welcome = WelcomePage()
        self._welcome.open_logs_requested.connect(self._on_open_logs)
        self._welcome.open_workspace_requested.connect(self._on_open_workspace)
        self._welcome.launch_demo_requested.connect(self._on_launch_demo)
        self._stack.addWidget(self._welcome)

        self._overview = OverviewPage()
        self._events = EventsPage()
        self._errors = ErrorsPage()
        self._correlation = CorrelationPage()
        self._alerts = AlertsPage()
        self._sources = SourcesPage()
        for w in (self._overview, self._events, self._errors, self._correlation, self._alerts, self._sources):
            self._stack.addWidget(w)
        rv.addWidget(self._stack, 1)
        hbox.addWidget(right, 1)

        self._stack.setCurrentWidget(self._welcome)
        self._list.setCurrentRow(-1)

        # Session events.
        self.session.subscribe(self._on_session_event)

    # ---- query bar ----

    def _build_query_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("background-color:#1a1d22; border-bottom:1px solid #2a2f36;")
        v = QtWidgets.QVBoxLayout(bar)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self._query_edit = QtWidgets.QLineEdit()
        self._query_edit.setPlaceholderText(
            'Try: level = ERROR AND service = "payments" | sort timestamp desc | limit 200'
        )
        self._query_edit.returnPressed.connect(self._on_run_query)
        row.addWidget(self._query_edit, 1)
        self._run_btn = QtWidgets.QPushButton("Run")
        self._run_btn.setObjectName("primary")
        self._run_btn.clicked.connect(self._on_run_query)
        row.addWidget(self._run_btn)
        self._save_btn = QtWidgets.QPushButton("Save…")
        self._save_btn.clicked.connect(self._on_save_query)
        row.addWidget(self._save_btn)
        self._history_btn = QtWidgets.QToolButton()
        self._history_btn.setText("Recent")
        self._history_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._history_menu = QtWidgets.QMenu(self)
        self._history_btn.setMenu(self._history_menu)
        row.addWidget(self._history_btn)
        v.addLayout(row)
        self._query_status = QtWidgets.QLabel("")
        self._query_status.setStyleSheet("color:#6c727b;")
        v.addWidget(self._query_status)
        return bar

    # ---- session ----

    def _on_session_event(self, event: str, payload) -> None:
        if event == "workspace-opened":
            summary = payload
            self._ws_label.setText(f"{summary.name}\n{summary.db_path}")
            self._set_pages_enabled(True)
            self._stack.setCurrentWidget(self._overview)
            self._list.setCurrentRow(0)
            for p in (self._overview, self._errors, self._correlation, self._alerts, self._sources):
                p.set_session(self.session)
        elif event == "source-ingested":
            self._overview.refresh()
            self._errors.refresh()
        elif event == "ingest-progress":
            self._status.setText(
                f"Ingesting {payload.source_path}: {payload.events_parsed} events "
                f"({payload.rate_events_per_s:.0f}/s)"
            )
        elif event == "query-finished":
            self._query_status.setText(f"{payload.row_count} rows in {payload.elapsed_ms:.1f} ms")

    def _set_pages_enabled(self, enabled: bool) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if enabled:
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)

    # ---- actions ----

    def _on_new_workspace(self) -> None:
        try:
            self.session.new_workspace()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "New workspace", str(e))
            return
        self._status.setText("Created new workspace.")
        self._refresh_history_menu()

    def _on_open_workspace(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open workspace", str(self.app_paths.workspaces_dir), "TraceForge workspace (*.trf)"
        )
        if not path:
            return
        try:
            self.session.open_workspace(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Open workspace", str(e))

    def _on_save_workspace(self) -> None:
        if not self.session.is_open():
            QtWidgets.QMessageBox.information(self, "Save", "No workspace is open.")
            return
        try:
            self.session.save_workspace()
            self._status.setText(f"Saved {self.session.workspace_path}.")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save", str(e))

    def _on_open_logs(self) -> None:
        if not self.session.is_open():
            self.session.new_workspace()
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Open log files",
            "",
            "Logs (*.log *.txt *.jsonl *.ndjson *.json *.csv);;All files (*)",
        )
        for f in files:
            self._ingest_async(f)

    def _ingest_async(self, path: str) -> None:
        if not self.session.is_open():
            return
        cancel = CancellationToken()
        self._ingest_cancels[path] = cancel

        def worker() -> None:
            try:
                self.session.ingest_source(path, cancel=cancel)
            except Exception as e:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_show_error",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, f"Ingest failed: {e}"),
                )
            finally:
                self._ingest_cancels.pop(path, None)

        class _Job(QtCore.QRunnable):
            def __init__(self, fn):
                super().__init__()
                self._fn = fn

            def run(self):
                self._fn()

        pool = QtCore.QThreadPool.globalInstance()
        pool.start(_Job(worker))

    def _show_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.warning(self, "TraceForge", msg)

    def _on_launch_demo(self) -> None:
        if not self.session.is_open():
            self.session.new_workspace(name="TraceForge Demo")
        target = self.app_paths.workspaces_dir / "demo"
        target.mkdir(parents=True, exist_ok=True)
        if not any(target.glob("*.log")):
            generate_demo(target, DemoConfig(event_count=20_000))
        files = sorted(target.glob("*.log"))
        for f in files:
            self._ingest_async(str(f))
        self._status.setText(f"Loading demo data from {target}…")

    def _on_export(self) -> None:
        if not self.session.is_open():
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export current result set",
            str(self.app_paths.session_export_dir() / "results.jsonl"),
            "JSONL (*.jsonl);;CSV (*.csv);;Parquet (*.parquet);;Session report (*.json *.md)",
        )
        if not path:
            return
        suffix = path.lower().rsplit(".", 1)[-1]
        from traceforge.export import export_query_rows, write_session_report

        if suffix in ("json", "md"):
            try:
                write_session_report(self.session.db, path, name=self.session.workspace.name)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Export", str(e))
            return
        fmt = {"csv": "csv", "parquet": "parquet"}.get(suffix, "jsonl")
        tfql = self._query_edit.text().strip() or 'severity IN ("ERROR","FATAL","CRITICAL")'
        try:
            result = self.session.run_query(tfql, base_limit=50_000)
        except QueryError as e:
            QtWidgets.QMessageBox.warning(self, "Export", str(e))
            return
        try:
            written = export_query_rows(
                self.session.db,
                result.rows,
                result.columns,
                path,
                fmt=fmt,
            )
            self._status.setText(f"Exported {written.row_count} rows to {written.path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export", str(e))

    def _on_page_changed(self, row: int) -> None:
        if row < 0:
            return
        widget = self._stack.widget(row + 1)  # +1 for welcome page
        self._stack.setCurrentWidget(widget)
        # Refresh pages that benefit from a refresh on entry.
        if widget is self._overview:
            self._overview.refresh()
        elif widget is self._errors:
            self._errors.refresh()
        elif widget is self._alerts:
            self._alerts.refresh()
        elif widget is self._sources:
            self._sources.refresh()

    def _on_run_query(self) -> None:
        if not self.session.is_open():
            return
        tfql = self._query_edit.text().strip()
        if not tfql:
            return
        self._stack.setCurrentWidget(self._events)
        self._list.setCurrentRow(1)
        try:
            res = self.session.run_query(tfql)
        except QueryError as e:
            self._query_status.setText(f"Query error: {e}")
            self._events.clear()
            return
        self._events.set_result(res)
        self._refresh_history_menu()

    def _on_save_query(self) -> None:
        if not self.session.is_open():
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save query", "Name:")
        if not ok or not name.strip():
            return
        self.session.save_query(name.strip(), self._query_edit.text().strip())
        self._refresh_history_menu()
        self._status.setText(f"Saved query '{name}'.")

    def _refresh_history_menu(self) -> None:
        self._history_menu.clear()
        if not self.session.is_open():
            return
        for q in self.session.workspace.saved_queries:
            action = QtGui.QAction(q.name, self)
            action.triggered.connect(lambda _checked=False, text=q.query: self._query_edit.setText(text))
            self._history_menu.addAction(action)

    # ---- close ----

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self.session.is_open() and self.session.workspace_path is not None:
                self.session.save_workspace()
        except Exception:
            pass
        try:
            if self._tailer is not None:
                self._tailer.stop()
        except Exception:
            pass
        self.session.close()
        super().closeEvent(event)
