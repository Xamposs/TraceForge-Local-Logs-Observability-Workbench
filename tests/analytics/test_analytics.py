"""Analytics tests: signatures, timeline, correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from traceforge.analytics import (
    build_hierarchy,
    collect_correlation,
    normalize_signature,
    severity_distribution,
    timeline,
    top_error_signatures,
    top_services,
)
from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database, EventRepository


def _seed(database: Database, events: list[LogEvent]) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/x", alias="x")
    fp = SourceFingerprint(path="/x", size=0, mtime_ns=0, sample_hash="h", content_kind="text")
    stats = SourceStats(path="/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    repo.insert_events(events, sid)


def test_normalize_signature_unchanged() -> None:
    msg = "Database connection refused"
    assert normalize_signature(msg) == "Database connection refused"


def test_normalize_signature_collapses_numbers() -> None:
    assert normalize_signature("Connection timeout after 5012ms") == normalize_signature(
        "Connection timeout after 4928ms"
    )


def test_normalize_signature_collapses_uuids() -> None:
    a = normalize_signature("User 123e4567-e89b-12d3-a456-426614174000 not found")
    b = normalize_signature("User 999e4567-e89b-12d3-a456-426614174000 not found")
    assert a == b


def test_normalize_signature_collapses_hex() -> None:
    a = normalize_signature("Trace 0xdeadbeefcafebabe failed")
    b = normalize_signature("Trace 0xfeedfacecafebabe failed")
    assert a == b


def test_normalize_signature_strips_ansi() -> None:
    assert normalize_signature("\x1b[31mERROR\x1b[0m: bad") == "ERROR: bad"


def test_timeline(database: Database) -> None:
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    events = [
        LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/x",
            line_number=i + 1,
            timestamp=now + timedelta(minutes=i),
            ingested_at=now,
            severity="INFO",
            message="x",
            raw_text="",
            service="api",
            raw_format="text",
        )
        for i in range(5)
    ]
    _seed(database, events)
    points = timeline(database, resolution="minute")
    assert len(points) == 5
    assert all(p.count == 1 for p in points)


def test_severity_distribution(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/x",
            line_number=i + 1,
            timestamp=now,
            ingested_at=now,
            severity=("ERROR" if i < 2 else "INFO"),
            message="x",
            raw_text="",
            raw_format="text",
        )
        for i in range(5)
    ]
    _seed(database, events)
    dist = severity_distribution(database)
    assert dict(dist).get("ERROR") == 2
    assert dict(dist).get("INFO") == 3


def test_top_services(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/x",
            line_number=i + 1,
            timestamp=now,
            ingested_at=now,
            severity="INFO",
            message="x",
            raw_text="",
            service=("api" if i < 3 else "payments"),
            raw_format="text",
        )
        for i in range(5)
    ]
    _seed(database, events)
    services = top_services(database, limit=5)
    assert services[0][0] == "api"
    assert services[0][1] == 3


def test_top_error_signatures(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/x",
            line_number=i + 1,
            timestamp=now,
            ingested_at=now,
            severity="ERROR",
            message=m,
            raw_text="",
            raw_format="text",
        )
        for i, m in enumerate(
            [
                "Connection timeout after 5012ms",
                "Connection timeout after 4928ms",
                "Database refused",
                "Connection timeout after 9999ms",
            ]
        )
    ]
    _seed(database, events)
    rows = top_error_signatures(database, limit=5)
    assert len(rows) == 2
    msgs = [r[0] for r in rows]
    assert "Database refused" in msgs


def test_correlation(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/x",
            line_number=i + 1,
            timestamp=now + timedelta(seconds=i),
            ingested_at=now,
            severity="INFO",
            message=f"m{i}",
            raw_text="",
            service=("api" if i < 2 else "payments"),
            trace_id=("t1" if i < 3 else None),
            span_id=("a" if i < 3 else None),
            parent_span_id=("root" if i == 1 else None),
            raw_format="text",
        )
        for i in range(4)
    ]
    _seed(database, events)
    rows = collect_correlation(database, trace_id="t1")
    assert len(rows) == 3
    groups = build_hierarchy(rows)
    assert any(g and g[0].service == "api" for g in groups)
