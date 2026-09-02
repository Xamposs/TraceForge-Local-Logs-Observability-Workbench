"""Tests for source-row identity and bulk-insert counting accuracy.

Section 6 — source ID reuse: re-ingesting the same path must yield the
same source_id; there must be no orphan source rows.
Section 7 — accurate inserted counts: ``insert_events`` returns the
true delta, not the optimistic batch size.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database, EventRepository


def _ev(i: int) -> LogEvent:
    now = datetime.now(tz=UTC)
    return LogEvent(
        event_id=f"e{i}",
        source="x",
        source_path="/tmp/x",
        line_number=i,
        timestamp=now,
        ingested_at=now,
        severity="INFO",
        message=f"msg {i}",
        raw_text=".",
        raw_format="text",
    )


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "t.duckdb")
    yield database
    database.close()


def test_same_path_yields_same_source_id(db: Database) -> None:
    repo = EventRepository(db)
    cfg = SourceConfig(path="/var/log/app.log", alias="app")
    sid1 = repo.get_or_create_source_id(cfg)
    sid2 = repo.get_or_create_source_id(cfg)
    assert sid1 == sid2
    # And only one source row exists.
    sources = repo.list_sources()
    assert len(sources) == 1
    assert sources[0][0] == sid1


def test_different_paths_get_different_source_ids(db: Database) -> None:
    repo = EventRepository(db)
    sid_a = repo.get_or_create_source_id(SourceConfig(path="/a", alias="a"))
    sid_b = repo.get_or_create_source_id(SourceConfig(path="/b", alias="b"))
    assert sid_a != sid_b


def test_ingest_source_does_not_orphan_sources(db: Database) -> None:
    """An ingested event must reference a real source row."""
    from traceforge.ingestion.pipeline import ingest_file

    f = __import__("pathlib").Path(db.path).parent / "app.log"
    f.write_text("2026-09-01 INFO hello\n2026-09-01 INFO world\n", encoding="utf-8")
    cfg = SourceConfig(path=str(f), alias="app")
    ingest_file(db, cfg)
    # Exactly one source row, and all events reference it.
    repo = EventRepository(db)
    sources = repo.list_sources()
    assert len(sources) == 1
    sid = sources[0][0]
    orphans = db.execute(
        "SELECT COUNT(*) FROM events e LEFT JOIN sources s ON s.id = e.source_id WHERE s.id IS NULL"
    ).fetchone()
    assert int(orphans[0]) == 0


def test_inserted_count_for_all_new_events(db: Database) -> None:
    repo = EventRepository(db)
    cfg = SourceConfig(path="/p", alias="p")
    sid = repo.get_or_create_source_id(cfg)
    events = [_ev(i) for i in range(1, 101)]
    n = repo.insert_events(events, sid)
    assert n == 100
    assert repo.count_events() == 100


def test_inserted_count_for_all_duplicates(db: Database) -> None:
    repo = EventRepository(db)
    cfg = SourceConfig(path="/p", alias="p")
    sid = repo.get_or_create_source_id(cfg)
    events = [_ev(i) for i in range(1, 101)]
    repo.insert_events(events, sid)
    # Re-insert the same events.
    n = repo.insert_events(events, sid)
    assert n == 0
    assert repo.count_events() == 100


def test_inserted_count_for_mixed_new_and_old(db: Database) -> None:
    repo = EventRepository(db)
    cfg = SourceConfig(path="/p", alias="p")
    sid = repo.get_or_create_source_id(cfg)
    # First 50 events.
    first = [_ev(i) for i in range(1, 51)]
    repo.insert_events(first, sid)
    # Second batch: 50 old + 50 new.
    second = [_ev(i) for i in range(1, 101)]
    n = repo.insert_events(second, sid)
    assert n == 50
    assert repo.count_events() == 100


def test_empty_batch_returns_zero(db: Database) -> None:
    repo = EventRepository(db)
    cfg = SourceConfig(path="/p", alias="p")
    sid = repo.get_or_create_source_id(cfg)
    assert repo.insert_events([], sid) == 0


def test_ingest_reuses_source_id_across_runs(db: Database, tmp_path) -> None:
    """Two ingestions of the same file path produce events with the
    same source_id; the second run is deduped by event_id PK."""
    from traceforge.ingestion.pipeline import ingest_file

    f = tmp_path / "app.log"
    f.write_text("2026-09-01 INFO once\n", encoding="utf-8")
    cfg = SourceConfig(path=str(f), alias="app")
    ingest_file(db, cfg)
    sid_1 = EventRepository(db).get_or_create_source_id(cfg)
    n1 = EventRepository(db).count_events()
    ingest_file(db, cfg)
    sid_2 = EventRepository(db).get_or_create_source_id(cfg)
    n2 = EventRepository(db).count_events()
    assert sid_1 == sid_2
    assert n1 == n2  # dedup'd


def test_upsert_source_does_not_overwrite_path(tmp_path) -> None:
    """The path UNIQUE constraint must be preserved; a re-upsert with
    the same source_id must not insert a duplicate source row."""
    db = Database(tmp_path / "t.duckdb")
    repo = EventRepository(db)
    cfg = SourceConfig(path="/p", alias="p")
    sid = repo.get_or_create_source_id(cfg)
    fp = SourceFingerprint(path="/p", size=10, mtime_ns=0, sample_hash="h", content_kind="text")
    stats = SourceStats(path="/p", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats, run_parsed=5, run_inserted=5)
    repo.upsert_source(sid, cfg, fp, "text", stats, run_parsed=3, run_inserted=3)
    sources = repo.list_sources()
    assert len(sources) == 1
    # The two upserts should have added their runs.
    row = sources[0]
    # row layout: id, path, alias, enabled, parser, size_bytes, mtime_ns,
    # sample_hash, content_kind, last_ingested_at, first_event_at,
    # last_event_at, last_byte_offset, total_events, parsed_events,
    # inserted_events, parse_errors, unstructured_lines, rejected_lines
    assert row[13] == 8  # total_events accumulated
    assert row[14] == 8  # parsed_events accumulated
    assert row[15] == 8  # inserted_events accumulated
    db.close()
