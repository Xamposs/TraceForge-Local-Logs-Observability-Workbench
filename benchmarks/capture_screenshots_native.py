"""Capture real Windows-native screenshots by launching the GUI
into the actual desktop session and grabbing the window.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Use the default Windows platform plugin (no QT_QPA_PLATFORM override).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6 import QtCore, QtWidgets

from traceforge.app_paths import AppPaths
from traceforge.demo import DemoConfig
from traceforge.demo import generate as generate_demo
from traceforge.ui.main_window import MainWindow
from traceforge.ui.session import Session


def main() -> int:
    assets = ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / "demo-logs"
    tmp.mkdir(parents=True, exist_ok=True)

    isolated = ROOT / ".demo-workspace"
    if isolated.exists():
        import shutil

        shutil.rmtree(isolated)
    paths = AppPaths(
        data_dir=isolated / "data",
        workspaces_dir=isolated / "data" / "workspaces",
        cache_dir=isolated / "data" / "cache",
        config_dir=isolated / "data" / "config",
        temp_dir=isolated / "data" / "tmp",
    )
    for p in (paths.data_dir, paths.workspaces_dir, paths.cache_dir, paths.config_dir, paths.temp_dir):
        p.mkdir(parents=True, exist_ok=True)

    if not any(tmp.glob("*.log")):
        generate_demo(tmp, DemoConfig(event_count=20_000, seed=20260901, duration_minutes=240))
    demo_files = sorted(tmp.glob("*.log"))

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TraceForge")
    from traceforge.ui.style import QSS

    app.setStyleSheet(QSS)

    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.4)

    win.session = Session(paths)
    win.session.new_workspace(name="TraceForge Demo")

    def _load_files():
        for p in demo_files:
            try:
                win.session.ingest_source(str(p))
            except Exception as e:
                print(f"warn: ingest {p}: {e}")
        win._on_session_event("workspace-opened", win.session.summary())
        for w in (win._overview, win._errors, win._correlation, win._alerts, win._sources):
            w.set_session(win.session)
        win._overview.refresh()

    QtCore.QTimer.singleShot(50, _load_files)
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.4)

    deadline = time.time() + 60
    while time.time() < deadline and win.session.summary().event_count < 19_000:
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.2)
    QtCore.QCoreApplication.processEvents()

    def grab(name: str) -> None:
        win._list.setCurrentRow(0)
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.2)
        win._overview.refresh()
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.3)
        win.grab().save(str(assets / name))
        print(f"saved {name}")

    # Overview
    win._list.setCurrentRow(0)
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.4)
    win._overview.refresh()
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.4)
    win.grab().save(str(assets / "screenshot-overview.png"))
    print("saved screenshot-overview.png")

    # Events
    win._query_edit.setText("level = ERROR | sort timestamp desc | limit 200")
    win._on_run_query()
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.5)
    win.grab().save(str(assets / "screenshot-events.png"))
    print("saved screenshot-events.png")

    # Query / aggregation
    win._query_edit.setText("level = ERROR | stats count() by service")
    win._on_run_query()
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.5)
    win.grab().save(str(assets / "screenshot-query.png"))
    print("saved screenshot-query.png")

    # Errors
    win._list.setCurrentRow(2)
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.3)
    win.grab().save(str(assets / "screenshot-errors.png"))
    print("saved screenshot-errors.png")

    # Correlation
    win._list.setCurrentRow(3)
    QtCore.QCoreApplication.processEvents()
    rel = win.session.db.execute("SELECT trace_id FROM events WHERE trace_id IS NOT NULL LIMIT 1")
    row = rel.fetchone()
    if row and row[0]:
        win._correlation.prefill(trace_id=row[0])
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.4)
    win.grab().save(str(assets / "screenshot-correlation.png"))
    print("saved screenshot-correlation.png")

    # Live tail / sources
    win._list.setCurrentRow(5)
    QtCore.QCoreApplication.processEvents()
    time.sleep(0.3)
    win.grab().save(str(assets / "screenshot-live-tail.png"))
    print("saved screenshot-live-tail.png")

    win.session.close()
    if isolated.exists():
        import shutil

        shutil.rmtree(isolated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
