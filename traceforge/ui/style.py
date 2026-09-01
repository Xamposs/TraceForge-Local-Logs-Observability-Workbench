"""Theme and stylesheet (dark graphite/slate)."""

from __future__ import annotations

QSS = """
* {
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
}
QMainWindow, QWidget#root {
    background-color: #1c1f24;
    color: #d6d9de;
}
QToolBar {
    background-color: #20242a;
    border: none;
    spacing: 4px;
    padding: 4px;
}
QStatusBar {
    background-color: #181b20;
    color: #8b9097;
    border-top: 1px solid #2a2f36;
}
QFrame#sidebar {
    background-color: #1a1d22;
    border-right: 1px solid #2a2f36;
}
QListWidget#sidebar {
    background: transparent;
    color: #c7cbd1;
    border: none;
    padding: 8px 0;
    outline: 0;
}
QListWidget#sidebar::item {
    padding: 10px 16px;
    border: none;
}
QListWidget#sidebar::item:selected {
    background-color: #2a323d;
    color: #ffffff;
    border-left: 3px solid #4a8cff;
}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #232830;
    color: #e3e6ea;
    border: 1px solid #2f343c;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #3a506e;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #4a8cff;
}
QPushButton {
    background-color: #2a313a;
    color: #d6d9de;
    border: 1px solid #353b44;
    border-radius: 4px;
    padding: 6px 14px;
    min-height: 22px;
}
QPushButton:hover {
    background-color: #323943;
}
QPushButton:pressed {
    background-color: #232830;
}
QPushButton#primary {
    background-color: #2c4e80;
    border-color: #3b6cad;
    color: #ffffff;
}
QPushButton#primary:hover {
    background-color: #355d96;
}
QTableView {
    background-color: #1a1d22;
    color: #d6d9de;
    gridline-color: #2a2f36;
    selection-background-color: #2c4e80;
    selection-color: #ffffff;
    alternate-background-color: #1e2228;
}
QHeaderView::section {
    background-color: #20242a;
    color: #aab0b8;
    border: 0;
    border-bottom: 1px solid #2a2f36;
    padding: 6px 8px;
    font-weight: 600;
}
QLabel#metricLabel {
    color: #6c727b;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
QLabel#metricValue {
    color: #ffffff;
    font-size: 22px;
    font-weight: 600;
}
QFrame#metricCard {
    background-color: #1f2329;
    border: 1px solid #2a2f36;
    border-radius: 6px;
}
QGroupBox {
    border: 1px solid #2a2f36;
    border-radius: 4px;
    margin-top: 12px;
    padding: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #aab0b8;
}
QSplitter::handle {
    background-color: #2a2f36;
}
QProgressBar {
    background-color: #232830;
    color: #ffffff;
    border: 1px solid #2a2f36;
    border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #2c4e80;
    border-radius: 3px;
}
QTabWidget::pane {
    border: 1px solid #2a2f36;
    background-color: #1a1d22;
}
QTabBar::tab {
    background: #1f2329;
    color: #8b9097;
    padding: 8px 16px;
    border: 1px solid #2a2f36;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #2a323d;
    color: #ffffff;
}
QToolTip {
    background-color: #232830;
    color: #e3e6ea;
    border: 1px solid #2a2f36;
    padding: 4px 6px;
}
QMenu {
    background-color: #1f2329;
    color: #d6d9de;
    border: 1px solid #2a2f36;
}
QMenu::item:selected {
    background-color: #2c4e80;
}
"""


SEVERITY_COLORS = {
    "TRACE": "#5d6770",
    "DEBUG": "#7a8290",
    "INFO": "#5b9bd5",
    "NOTICE": "#5b9bd5",
    "WARN": "#d8a13a",
    "WARNING": "#d8a13a",
    "ERROR": "#e26161",
    "FATAL": "#ff5252",
    "CRITICAL": "#ff5252",
    "UNKNOWN": "#6c727b",
}
