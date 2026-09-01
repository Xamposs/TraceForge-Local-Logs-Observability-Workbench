"""Correlation view.

Given one or more correlation IDs (trace_id, request_id, session_id),
return the events that share the ID, in chronological order. If
``parent_span_id`` is present, build a hierarchy; otherwise produce a flat
timeline grouped by service.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from traceforge.storage import Database


@dataclass
class CorrelatedEvent:
    event_id: str
    service: str | None
    timestamp: datetime | None
    severity: str
    message: str
    source: str
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    request_id: str | None
    duration_ms: float | None
    status_code: int | None


def collect_correlation(
    db: Database,
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
) -> list[CorrelatedEvent]:
    clauses: list[str] = []
    params: list = []
    if trace_id is not None:
        clauses.append("trace_id = ?")
        params.append(trace_id)
    if request_id is not None:
        clauses.append("request_id = ?")
        params.append(request_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if not clauses:
        return []
    sql = (
        "SELECT event_id, service, timestamp, severity, message, source_alias,"
        " trace_id, span_id, parent_span_id, request_id, duration_ms, status_code"
        " FROM events WHERE " + " OR ".join(clauses) + " ORDER BY timestamp ASC NULLS LAST, line_number ASC"
    )
    rel = db.execute(sql, params)
    out: list[CorrelatedEvent] = []
    for row in rel.fetchall():
        out.append(
            CorrelatedEvent(
                event_id=row[0],
                service=row[1],
                timestamp=row[2],
                severity=row[3],
                message=row[4],
                source=row[5],
                trace_id=row[6],
                span_id=row[7],
                parent_span_id=row[8],
                request_id=row[9],
                duration_ms=row[10],
                status_code=row[11],
            )
        )
    return out


def build_hierarchy(events: list[CorrelatedEvent]) -> list[list[CorrelatedEvent]]:
    """Group events into a chronological sequence per service (no claim of
    true parent/child relationships unless parent_span_id is present)."""
    by_service: dict[str | None, list[CorrelatedEvent]] = defaultdict(list)
    for e in events:
        by_service[e.service].append(e)
    ordered_services = sorted(
        by_service.keys(),
        key=lambda s: min((e.timestamp for e in by_service[s] if e.timestamp), default=datetime.max),
    )
    return [by_service[s] for s in ordered_services]
