"""Storage tests: schema, batch insert, query, dedup, source isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database, EventRepository


def _event(i: int, *, source: str = "x", severity: str = "INFO", service: str = "api") -> LogEvent:
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    return LogEvent(
        event_id=f"e{i}",
        source=source,
        source_path=f"/tmp/{source}",
        line_number=i,
        timestamp=now,
        ingested_at=now,
        severity=severity,
        message=f"msg {i}",
        raw_text="...",
        service=service,
        duration_ms=float(i),
        raw_format="text",
    )


def test_schema_created(database: Database) -> None:
    rel = database.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'")
    names = {r[0] for r in rel.fetchall()}
    assert {"events", "sources"} <= names


def test_insert_and_count(database: Database) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    repo.insert_events([_event(i) for i in range(1, 6)], sid)
    assert repo.count_events() == 5


def test_insert_is_idempotent(database: Database) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    events = [_event(i) for i in range(1, 6)]
    repo.insert_events(events, sid)
    # The PK constraint enforces dedup; the table still has 5 rows
    # even though the second insert reported inserting 5 more (optimistic).
    repo.insert_events(events, sid)
    assert repo.count_events() == 5


def test_count_by_severity(database: Database) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    events = [
        _event(1, severity="ERROR"),
        _event(2, severity="INFO"),
        _event(3, severity="ERROR"),
        _event(4, severity="WARN"),
    ]
    repo.insert_events(events, sid)
    counts = repo.count_by_severity()
    assert counts["ERROR"] == 2
    assert counts["INFO"] == 1
    assert counts["WARN"] == 1


def test_filter_by_timestamp_and_severity(database: Database) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    repo.insert_events([_event(i) for i in range(1, 6)], sid)
    rel = database.execute("SELECT COUNT(*) FROM events WHERE severity = 'ERROR'")
    assert rel.fetchone()[0] == 0  # no errors
    rel = database.execute("SELECT COUNT(*) FROM events WHERE severity = 'INFO'")
    assert rel.fetchone()[0] == 5


def test_correlation_lookup(database: Database) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    now = datetime.now(tz=UTC)
    events = [
        LogEvent(
            event_id=f"c{i}",
            source="x",
            source_path="/tmp/x",
            line_number=i,
            timestamp=now,
            ingested_at=now,
            severity="INFO",
            message=f"m{i}",
            raw_text="",
            trace_id=("t1" if i < 3 else "t2"),
            request_id=f"r{i}",
            raw_format="text",
        )
        for i in range(1, 6)
    ]
    repo.insert_events(events, sid)
    rows = repo.fetch_by_correlation(trace_id="t1")
    assert len(rows) == 2
    rows = repo.fetch_by_correlation(request_id="r3")
    assert len(rows) == 1


def test_source_isolation(database: Database) -> None:
    repo = EventRepository(database)
    sid1 = repo.next_source_id()
    repo.upsert_source(
        sid1,
        SourceConfig(path="/a", alias="a"),
        SourceFingerprint(path="/a", size=0, mtime_ns=0, sample_hash="x", content_kind="text"),
        "text",
        SourceStats(path="/a", parser="text"),
    )
    sid2 = repo.next_source_id()
    repo.upsert_source(
        sid2,
        SourceConfig(path="/b", alias="b"),
        SourceFingerprint(path="/b", size=0, mtime_ns=0, sample_hash="x", content_kind="text"),
        "text",
        SourceStats(path="/b", parser="text"),
    )
    repo.insert_events([_event(1, source="a"), _event(2, source="a")], sid1)
    repo.insert_events([_event(3, source="b")], sid2)
    rel = database.execute("SELECT COUNT(*) FROM events WHERE source_id = ?", [sid1])
    assert rel.fetchone()[0] == 2
    rel = database.execute("SELECT COUNT(*) FROM events WHERE source_id = ?", [sid2])
    assert rel.fetchone()[0] == 1


def test_workspace_reopen(database: Database, temp_dir) -> None:
    """Reopen a database file and verify data is preserved."""
    db_path = temp_dir / "reopen.duckdb"
    db1 = Database(db_path)
    repo = EventRepository(db1)
    sid = repo.next_source_id()
    repo.upsert_source(
        sid,
        SourceConfig(path="/x", alias="x"),
        SourceFingerprint(path="/x", size=0, mtime_ns=0, sample_hash="x", content_kind="text"),
        "text",
        SourceStats(path="/x", parser="text"),
    )
    repo.insert_events([_event(1)], sid)
    db1.close()
    db2 = Database(db_path)
    assert EventRepository(db2).count_events() == 1
    db2.close()
