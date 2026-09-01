"""Severity helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from traceforge.storage import Database


def severity_counts(db: Database) -> dict[str, int]:
    rel = db.execute("SELECT severity, COUNT(*) FROM events GROUP BY severity")
    return {row[0]: int(row[1]) for row in rel.fetchall()}


def severity_over_time(
    db: Database,
    *,
    bucket: str = "minute",
    severities: Iterable[str] = ("ERROR", "WARN", "INFO"),
) -> list[tuple[datetime, str, int]]:
    sev_list = list(severities)
    if not sev_list:
        return []
    rel = db.execute(
        "SELECT date_trunc(?, timestamp) AS b, severity, COUNT(*)"
        " FROM events WHERE timestamp IS NOT NULL AND severity IN ("
        + ",".join("?" for _ in sev_list)
        + ") GROUP BY b, severity ORDER BY b",
        [bucket, *sev_list],
    )
    return [(row[0], row[1], int(row[2])) for row in rel.fetchall()]
