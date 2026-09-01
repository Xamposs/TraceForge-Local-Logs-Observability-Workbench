"""Aggregation helpers: timeline, severity, top-N.

All aggregations run as parameterized SQL against DuckDB. The output is
small (one row per bucket) and suitable for direct plotting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from traceforge.storage import Database

Resolution = Literal["second", "minute", "five_minutes", "hour", "day"]

_RESOLUTION_TO_TRUNC = {
    "second": "second",
    "minute": "minute",
    "five_minutes": None,  # computed as floor(minute / 5) * 5
    "hour": "hour",
    "day": "day",
}


def _bucket_sql(resolution: Resolution) -> str:
    if resolution == "five_minutes":
        # floor by 5 minutes
        return "(date_trunc('minute', timestamp) - INTERVAL (MINUTE(timestamp) % 5) MINUTE)"
    return f"date_trunc('{_RESOLUTION_TO_TRUNC[resolution]}', timestamp)"


@dataclass
class TimelinePoint:
    bucket: datetime
    count: int
    errors: int
    warnings: int


def timeline(
    db: Database,
    *,
    resolution: Resolution = "minute",
    start: datetime | None = None,
    end: datetime | None = None,
    source_id: int | None = None,
    limit: int = 5000,
) -> list[TimelinePoint]:
    """Return aggregated timeline buckets with error/warning breakdown."""
    bucket = _bucket_sql(resolution)
    where = ["timestamp IS NOT NULL"]
    params: list[Any] = []
    if start is not None:
        where.append("timestamp >= ?")
        params.append(start)
    if end is not None:
        where.append("timestamp < ?")
        params.append(end)
    if source_id is not None:
        where.append("source_id = ?")
        params.append(source_id)
    sql = (
        f"SELECT {bucket} AS b, COUNT(*) AS c,"
        f" SUM(CASE WHEN severity IN ('ERROR','FATAL','CRITICAL') THEN 1 ELSE 0 END) AS errs,"
        f" SUM(CASE WHEN severity IN ('WARN','WARNING') THEN 1 ELSE 0 END) AS warns"
        f" FROM events WHERE {' AND '.join(where)}"
        f" GROUP BY b ORDER BY b LIMIT {int(limit)}"
    )
    rel = db.execute(sql, params)
    out: list[TimelinePoint] = []
    for row in rel.fetchall():
        out.append(
            TimelinePoint(
                bucket=row[0],
                count=int(row[1] or 0),
                errors=int(row[2] or 0),
                warnings=int(row[3] or 0),
            )
        )
    return out


def severity_distribution(db: Database) -> list[tuple[str, int]]:
    rel = db.execute("SELECT severity, COUNT(*) FROM events GROUP BY severity ORDER BY 2 DESC")
    return [(r[0], int(r[1])) for r in rel.fetchall()]


def top_services(db: Database, limit: int = 10) -> list[tuple[str, int]]:
    rel = db.execute(
        "SELECT service, COUNT(*) AS c FROM events WHERE service IS NOT NULL"
        " GROUP BY service ORDER BY c DESC LIMIT ?",
        [int(limit)],
    )
    return [(r[0], int(r[1])) for r in rel.fetchall()]


def top_error_signatures(
    db: Database,
    limit: int = 20,
) -> list[tuple[str, int, datetime | None, datetime | None]]:
    """Group top recurring error messages, collapsing by normalized signature.

    Two messages that differ only in dynamic values (numbers, UUIDs, hex IDs,
    timestamps) collapse to a single signature row.
    """
    from collections import defaultdict

    from traceforge.analytics.signatures import normalize_signature

    rel = db.execute(
        "SELECT message, COUNT(*) AS c, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts"
        " FROM events WHERE severity IN ('ERROR','FATAL','CRITICAL')"
        " GROUP BY message ORDER BY c DESC LIMIT 5000"
    )
    groups: dict[str, list[tuple[int, datetime | None, datetime | None]]] = defaultdict(list)
    for row in rel.fetchall():
        msg = row[0] or ""
        sig = normalize_signature(msg)
        groups[sig].append((int(row[1]), row[2], row[3]))
    out: list[tuple[str, int, datetime | None, datetime | None]] = []
    for sig, items in groups.items():
        total = sum(c for c, _, _ in items)
        firsts = [f for _, f, _ in items if f is not None]
        lasts = [l for _, _, l in items if l is not None]
        first_ts = min(firsts) if firsts else None
        last_ts = max(lasts) if lasts else None
        out.append((sig, total, first_ts, last_ts))
    out.sort(key=lambda r: -r[1])
    return out[: int(limit)]
