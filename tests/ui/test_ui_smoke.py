"""UI smoke tests using pytest-qt (offscreen platform)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.ui


@pytest.fixture(scope="session", autouse=True)
def _ensure_qapp():
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


def test_main_window_instantiates(_ensure_qapp) -> None:
    from traceforge.ui.main_window import MainWindow

    win = MainWindow()
    assert win.isVisible() is False  # we did not call show()
    win.show()
    _ensure_qapp.processEvents()
    assert win.isVisible()


def test_session_open_and_summarize(_ensure_qapp, tmp_path: Path) -> None:
    from traceforge.app_paths import AppPaths
    from traceforge.ui.session import Session

    paths = AppPaths(
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "data" / "workspaces",
        cache_dir=tmp_path / "data" / "cache",
        config_dir=tmp_path / "data" / "config",
        temp_dir=tmp_path / "data" / "tmp",
    )
    for p in (paths.data_dir, paths.workspaces_dir, paths.cache_dir, paths.config_dir, paths.temp_dir):
        p.mkdir(parents=True, exist_ok=True)
    session = Session(paths)
    session.new_workspace("smoke")
    summary = session.summary()
    assert summary.event_count == 0
    assert summary.name == "smoke"


def test_workspace_round_trip(_ensure_qapp, tmp_path: Path) -> None:
    from traceforge.workspace_io import load_workspace, new_workspace, save_workspace

    ws = new_workspace("rt")
    p = tmp_path / "rt.trf"
    save_workspace(ws, p)
    ws2 = load_workspace(p)
    assert ws2.name == "rt"


def test_event_table_model_headers() -> None:
    from traceforge.ui.models.event_model import EventTableModel

    m = EventTableModel()
    assert m.columnCount() == 0
    assert m.rowCount() == 0
