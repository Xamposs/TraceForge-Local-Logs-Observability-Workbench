"""Central application object — GUI launcher."""

from __future__ import annotations

import os
import sys


def launch() -> int:
    """Launch the PySide6 desktop application."""
    # Set the Qt platform automatically; allow override via env var.
    if not os.environ.get("QT_QPA_PLATFORM") and not _has_display():
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TraceForge")
    app.setApplicationDisplayName("TraceForge")
    app.setOrganizationName("TraceForge")
    app.setStyle("Fusion")
    from traceforge.ui.style import QSS

    app.setStyleSheet(QSS)
    from traceforge.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()


def _has_display() -> bool:
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
