"""Welcome / empty-state page shown when no workspace is open."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class WelcomePage(QtWidgets.QWidget):
    open_logs_requested = QtCore.Signal()
    open_workspace_requested = QtCore.Signal()
    launch_demo_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        banner = QtWidgets.QLabel()
        banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        banner.setTextFormat(QtCore.Qt.TextFormat.RichText)
        banner.setText(
            """
            <div style='text-align:center;'>
              <div style='font-size:42px; letter-spacing:0.25em; color:#e3e6ea;'>TRACEFORGE</div>
              <div style='font-size:18px; color:#aab0b8; margin-top:6px;'>Local Logs &amp; Observability Workbench</div>
              <div style='font-size:14px; color:#6c727b; margin-top:18px; letter-spacing:0.08em;'>Inspect &middot; Query &middot; Correlate &middot; Understand</div>
            </div>
            """
        )
        banner.setMinimumHeight(260)
        layout.addWidget(banner)

        button_row = QtWidgets.QWidget()
        buttons = QtWidgets.QHBoxLayout(button_row)
        buttons.setContentsMargins(0, 12, 0, 12)
        buttons.setSpacing(12)
        buttons.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._b_open = QtWidgets.QPushButton("Open Logs")
        self._b_open.setObjectName("primary")
        self._b_open.setMinimumWidth(180)
        self._b_ws = QtWidgets.QPushButton("Open Workspace")
        self._b_ws.setMinimumWidth(180)
        self._b_demo = QtWidgets.QPushButton("Launch Demo")
        self._b_demo.setObjectName("primary")
        self._b_demo.setMinimumWidth(180)
        buttons.addWidget(self._b_open)
        buttons.addWidget(self._b_ws)
        buttons.addWidget(self._b_demo)
        layout.addWidget(button_row)

        facts = QtWidgets.QLabel()
        facts.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        facts.setText(
            "<div style='color:#6c727b; font-size:12px; letter-spacing:0.08em;'>"
            "100% Local  &nbsp;·&nbsp;  Read-Only Sources  &nbsp;·&nbsp;  No Cloud  &nbsp;·&nbsp;  No Telemetry"
            "</div>"
        )
        layout.addWidget(facts)
        layout.addStretch(1)

        self._b_open.clicked.connect(self.open_logs_requested)
        self._b_ws.clicked.connect(self.open_workspace_requested)
        self._b_demo.clicked.connect(self.launch_demo_requested)
