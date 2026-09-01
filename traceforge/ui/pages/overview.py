"""Overview page — top-level metrics, timeline, top services & error signatures."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtGui, QtWidgets

from traceforge.analytics import (
    severity_distribution,
    timeline,
    top_error_signatures,
    top_services,
)
from traceforge.analytics.signatures import normalize_signature
from traceforge.analytics.timeline import Resolution
from traceforge.storage import Database
from traceforge.ui.session import Session
from traceforge.ui.style import SEVERITY_COLORS


def _format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:,}"


class MetricCard(QtWidgets.QFrame):
    def __init__(self, label: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        self._label = QtWidgets.QLabel(label.upper())
        self._label.setObjectName("metricLabel")
        self._value = QtWidgets.QLabel("0")
        self._value.setObjectName("metricValue")
        v.addWidget(self._label)
        v.addWidget(self._value)

    def setValue(self, value: str) -> None:
        self._value.setText(value)


class OverviewPage(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        self._resolution: Resolution = "minute"

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Top metric row.
        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(12)
        self._card_total = MetricCard("Total events")
        self._card_errors = MetricCard("Errors")
        self._card_warnings = MetricCard("Warnings")
        self._card_sources = MetricCard("Sources")
        self._card_range = MetricCard("Time range")
        for c in (
            self._card_total,
            self._card_errors,
            self._card_warnings,
            self._card_sources,
            self._card_range,
        ):
            metrics.addWidget(c)
        root.addLayout(metrics)

        # Controls row.
        controls = QtWidgets.QHBoxLayout()
        self._resolution_combo = QtWidgets.QComboBox()
        self._resolution_combo.addItems(["second", "minute", "five_minutes", "hour", "day"])
        self._resolution_combo.setCurrentText("minute")
        self._resolution_combo.currentTextChanged.connect(self._on_resolution_changed)
        controls.addWidget(QtWidgets.QLabel("Timeline resolution:"))
        controls.addWidget(self._resolution_combo)
        controls.addStretch(1)
        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        controls.addWidget(self._refresh_btn)
        root.addLayout(controls)

        # Charts.
        charts = QtWidgets.QHBoxLayout()
        charts.setSpacing(12)
        self._timeline_plot = pg.PlotWidget()
        self._timeline_plot.setBackground("#1a1d22")
        self._timeline_plot.showGrid(x=True, y=True, alpha=0.15)
        self._timeline_plot.setLabel("left", "Events")
        self._timeline_plot.setLabel("bottom", "Time")
        self._timeline_plot.addLegend(offset=(10, 10))
        self._severity_bars = pg.PlotWidget()
        self._severity_bars.setBackground("#1a1d22")
        self._severity_bars.showGrid(x=False, y=True, alpha=0.15)
        self._severity_bars.setLabel("left", "Count")
        self._severity_bars.setLabel("bottom", "Severity")
        charts.addWidget(self._timeline_plot, 2)
        charts.addWidget(self._severity_bars, 1)
        root.addLayout(charts, 1)

        # Lower row: top services + top error signatures + recent activity.
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(12)
        self._top_services = self._make_list_panel("Top services")
        self._top_signatures = self._make_list_panel("Top error signatures")
        self._recent = self._make_list_panel("Recent activity")
        bottom.addWidget(self._top_services, 1)
        bottom.addWidget(self._top_signatures, 1)
        bottom.addWidget(self._recent, 1)
        root.addLayout(bottom, 1)

    def _make_list_panel(self, title: str) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        tree = QtWidgets.QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        v.addWidget(tree)
        return box

    def set_session(self, session: Session | None) -> None:
        self._session = session
        self.refresh()

    def _on_resolution_changed(self, value: str) -> None:
        self._resolution = value  # type: ignore[assignment]
        self.refresh()

    def refresh(self) -> None:
        if self._session is None or not self._session.is_open():
            return
        try:
            summary = self._session.summary()
        except Exception:
            return
        self._card_total.setValue(_format_count(summary.event_count))
        sev = summary.severity_counts
        self._card_errors.setValue(
            _format_count(sev.get("ERROR", 0) + sev.get("FATAL", 0) + sev.get("CRITICAL", 0))
        )
        self._card_warnings.setValue(_format_count(sev.get("WARN", 0) + sev.get("WARNING", 0)))
        self._card_sources.setValue(_format_count(len(summary.sources)))
        rng = summary.time_range
        if rng[0] and rng[1]:
            self._card_range.setValue(f"{_format_count(int((rng[1]-rng[0]).total_seconds()))}s")
        else:
            self._card_range.setValue("-")

        db = self._session.db
        self._render_timeline(db)
        self._render_severity_bars(db)
        self._render_top_services(db)
        self._render_top_signatures(db)
        self._render_recent(db)

    def _render_timeline(self, db: Database) -> None:
        self._timeline_plot.clear()
        try:
            points = timeline(db, resolution=self._resolution)
        except Exception:
            return
        if not points:
            return
        xs = [p.bucket.timestamp() for p in points]
        total = [p.count for p in points]
        errors = [p.errors for p in points]
        warns = [p.warnings for p in points]
        self._timeline_plot.plot(xs, total, pen=pg.mkPen("#5b9bd5", width=2), name="All")
        self._timeline_plot.plot(xs, errors, pen=pg.mkPen("#e26161", width=2), name="Errors")
        self._timeline_plot.plot(xs, warns, pen=pg.mkPen("#d8a13a", width=2), name="Warnings")

    def _render_severity_bars(self, db: Database) -> None:
        self._severity_bars.clear()
        try:
            sev = severity_distribution(db)
        except Exception:
            return
        if not sev:
            return
        labels = [s for s, _ in sev]
        counts = [c for _, c in sev]
        x_positions = list(range(len(labels)))
        bg = pg.BarGraphItem(
            x=x_positions,
            height=counts,
            width=0.6,
            brushes=[pg.mkBrush(SEVERITY_COLORS.get(l, "#5b9bd5")) for l in labels],
        )
        self._severity_bars.addItem(bg)
        ax = self._severity_bars.getAxis("bottom")
        ax.setTicks([list(zip(x_positions, labels, strict=False))])

    def _render_top_services(self, db: Database) -> None:
        tree: QtWidgets.QTreeWidget = self._top_services.findChild(QtWidgets.QTreeWidget)
        tree.clear()
        try:
            rows = top_services(db, limit=10)
        except Exception:
            rows = []
        for name, c in rows:
            item = QtWidgets.QTreeWidgetItem([f"{name}   {_format_count(c)}"])
            tree.addTopLevelItem(item)

    def _render_top_signatures(self, db: Database) -> None:
        tree: QtWidgets.QTreeWidget = self._top_signatures.findChild(QtWidgets.QTreeWidget)
        tree.clear()
        try:
            rows = top_error_signatures(db, limit=10)
        except Exception:
            rows = []
        for msg, c, _first, _last in rows:
            sig = normalize_signature(msg)
            short = (sig[:60] + "…") if len(sig) > 60 else sig
            item = QtWidgets.QTreeWidgetItem([f"{short}   {_format_count(c)}"])
            tree.addTopLevelItem(item)

    def _render_recent(self, db: Database) -> None:
        tree: QtWidgets.QTreeWidget = self._recent.findChild(QtWidgets.QTreeWidget)
        tree.clear()
        rel = db.execute(
            "SELECT timestamp, severity, service, message FROM events"
            " ORDER BY timestamp DESC NULLS LAST, line_number DESC LIMIT 12"
        )
        for row in rel.fetchall():
            ts = row[0]
            when = ts.strftime("%H:%M:%S") if ts else "-"
            sev = row[1] or "UNKNOWN"
            svc = row[2] or "-"
            msg = (row[3] or "")[:60]
            item = QtWidgets.QTreeWidgetItem([f"{when}  {sev:7s}  {svc:8s}  {msg}"])
            item.setForeground(0, QtGui.QColor(SEVERITY_COLORS.get(sev, "#aab0b8")))
            tree.addTopLevelItem(item)
