"""Export module: CSV, JSONL, Parquet, session report."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from traceforge.analytics import (
    severity_distribution,
    top_error_signatures,
    top_services,
)
from traceforge.models.alerts import Alert
from traceforge.storage import Database


@dataclass
class ExportResult:
    path: Path
    row_count: int
    format: str


def export_query_rows(
    db: Database,
    rows: Iterable[tuple],
    columns: list[str],
    output: str | os.PathLike[str],
    *,
    fmt: str = "jsonl",
) -> ExportResult:
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    if fmt == "jsonl":
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                obj = {col: _coerce(row[i]) for i, col in enumerate(columns)}
                f.write(json.dumps(obj, ensure_ascii=False, default=str))
                f.write("\n")
                n += 1
    elif fmt == "csv":
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(columns)
            for row in rows:
                w.writerow([_coerce(v) for v in row])
                n += 1
    elif fmt == "parquet":
        try:
            import pyarrow as pa  # type: ignore
            import pyarrow.parquet as pq  # type: ignore
        except ImportError as e:
            raise RuntimeError("Parquet export requires the optional 'pyarrow' package") from e
        data: dict[str, list] = {c: [] for c in columns}
        for row in rows:
            for i, c in enumerate(columns):
                data[c].append(_coerce(row[i]))
            n += 1
        table = pa.table(data)
        pq.write_table(table, str(p))
    else:
        raise ValueError(f"Unknown export format: {fmt}")
    return ExportResult(path=p, row_count=n, format=fmt)


def _coerce(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def write_session_report(
    db: Database,
    output: str | os.PathLike[str],
    *,
    alerts: list[Alert] | None = None,
    name: str = "TraceForge Session",
) -> Path:
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    counts = dict(severity_distribution(db))
    services = top_services(db, limit=10)
    sigs = top_error_signatures(db, limit=20)
    rel = db.execute("SELECT COUNT(*) FROM events")
    total_events = int(rel.fetchone()[0] or 0)
    rel = db.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE timestamp IS NOT NULL")
    row = rel.fetchone()
    start_ts, end_ts = row[0], row[1]
    payload = {
        "name": name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "event_count": total_events,
        "time_range": {
            "start": start_ts.isoformat() if start_ts else None,
            "end": end_ts.isoformat() if end_ts else None,
        },
        "severity_counts": counts,
        "top_services": [{"service": s, "count": c} for s, c in services],
        "top_error_signatures": [
            {
                "signature": sig,
                "count": c,
                "first_seen": f.isoformat() if f else None,
                "last_seen": l.isoformat() if l else None,
            }
            for sig, c, f, l in sigs
        ],
        "alerts": [
            {
                "id": a.id,
                "rule_id": a.rule_id,
                "rule_name": a.rule_name,
                "severity": a.severity,
                "fired_at": a.fired_at.isoformat() if a.fired_at else None,
                "title": a.title,
                "explanation": a.explanation,
                "threshold": a.threshold,
                "observed": a.observed,
            }
            for a in (alerts or [])
        ],
    }
    if str(p).endswith(".md"):
        p.write_text(_render_markdown(payload), encoding="utf-8")
    else:
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def _render_markdown(p: dict) -> str:
    lines: list[str] = []
    lines.append(f"# {p['name']}")
    lines.append("")
    lines.append(f"_Generated at {p['generated_at']}_")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Events: **{p['event_count']}**")
    if p["time_range"]["start"] and p["time_range"]["end"]:
        lines.append(f"- Time range: `{p['time_range']['start']}` → `{p['time_range']['end']}`")
    lines.append("")
    if p["severity_counts"]:
        lines.append("## Severity")
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev, c in sorted(p["severity_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"| {sev} | {c} |")
        lines.append("")
    if p["top_services"]:
        lines.append("## Top services")
        lines.append("")
        lines.append("| Service | Count |")
        lines.append("|---------|-------|")
        for s in p["top_services"]:
            lines.append(f"| {s['service']} | {s['count']} |")
        lines.append("")
    if p["top_error_signatures"]:
        lines.append("## Top error signatures")
        lines.append("")
        lines.append("| Signature | Count | First seen | Last seen |")
        lines.append("|-----------|-------|------------|-----------|")
        for s in p["top_error_signatures"][:10]:
            sig = (s["signature"] or "")[:60].replace("|", "\\|")
            lines.append(f"| {sig} | {s['count']} | {s['first_seen']} | {s['last_seen']} |")
        lines.append("")
    if p["alerts"]:
        lines.append("## Alerts")
        lines.append("")
        for a in p["alerts"]:
            lines.append(f"### {a['rule_name']} ({a['severity']})")
            lines.append("")
            lines.append(f"- When: {a['fired_at']}")
            lines.append(f"- {a['title']}")
            lines.append(f"- Threshold: {a['threshold']}")
            lines.append(f"- Observed: {a['observed']}")
            lines.append("")
            lines.append(f"  {a['explanation']}")
            lines.append("")
    return "\n".join(lines) + "\n"


__all__ = ["ExportResult", "export_query_rows", "write_session_report"]
