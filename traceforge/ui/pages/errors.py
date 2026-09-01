"""Error signatures page."""

from __future__ import annotations

from PySide6 import QtWidgets

from traceforge.analytics.signatures import normalize_signature
from traceforge.analytics.timeline import top_error_signatures
from traceforge.ui.session import Session


class ErrorsPage(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        header = QtWidgets.QLabel("Top recurring error signatures")
        header.setStyleSheet("font-weight:600; color:#aab0b8;")
        v.addWidget(header)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(["Signature", "Count", "First seen", "Last seen"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        v.addWidget(self._tree, 1)
        self._sample = QtWidgets.QTextEdit()
        self._sample.setReadOnly(True)
        self._sample.setPlaceholderText("Select a signature to see sample events.")
        v.addWidget(self._sample, 1)

    def set_session(self, session: Session | None) -> None:
        self._session = session
        self.refresh()

    def refresh(self) -> None:
        if self._session is None or not self._session.is_open():
            return
        self._tree.clear()
        try:
            rows = top_error_signatures(self._session.db, limit=50)
        except Exception:
            rows = []
        for msg, c, first, last in rows:
            sig = normalize_signature(msg)
            short = (sig[:160] + "…") if len(sig) > 160 else sig
            item = QtWidgets.QTreeWidgetItem(
                [
                    short,
                    str(c),
                    first.strftime("%Y-%m-%d %H:%M:%S") if first else "-",
                    last.strftime("%Y-%m-%d %H:%M:%S") if last else "-",
                ]
            )
            self._tree.addTopLevelItem(item)

    def _on_selection(self) -> None:
        if self._session is None:
            return
        items = self._tree.selectedItems()
        if not items:
            return
        sig = items[0].text(0)
        rel = self._session.db.execute(
            "SELECT timestamp, severity, service, message FROM events"
            " WHERE severity IN ('ERROR','FATAL','CRITICAL')"
            " AND message LIKE ? ORDER BY timestamp DESC LIMIT 20",
            [f"%{sig[:60]}%"],
        )
        rows = ["<style>td{padding:2px 8px;}</style><table>"]
        for r in rel.fetchall():
            ts = r[0].strftime("%Y-%m-%d %H:%M:%S") if r[0] else "-"
            sev = r[1] or "?"
            svc = r[2] or "-"
            msg = (r[3] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            rows.append(f"<tr><td>{ts}</td><td>{sev}</td><td>{svc}</td><td>{msg}</td></tr>")
        rows.append("</table>")
        self._sample.setHtml("".join(rows))
