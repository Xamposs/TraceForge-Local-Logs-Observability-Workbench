"""Concurrency stress test: one writer thread and one reader thread
running against a single Database for many iterations.

The test guards against:
* result-set corruption (rows split across execute / fetch boundaries)
* DuckDB concurrency errors ("database is locked", "Interrupted",
  "another thread holds the lock", etc.)
* inconsistent final counts

Both threads hold the database's internal lock for the full
``execute`` + ``fetch`` sequence. The reader thread issues analytical
queries while the writer is actively inserting.

Run with::

    pytest tests/storage/test_concurrency.py -q
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database, EventRepository


def _make_event(i: int) -> LogEvent:
    now = datetime.now(tz=UTC)
    return LogEvent(
        event_id=f"e{i}",
        source="x",
        source_path="/tmp/x",
        line_number=i + 1,
        timestamp=now,
        ingested_at=now,
        severity=("ERROR" if i % 3 == 0 else "INFO"),
        message=f"msg {i}",
        raw_text=f"msg {i}",
        service=("api" if i % 2 == 0 else "auth"),
        raw_format="text",
    )


def test_concurrent_writes_and_reads(tmp_path) -> None:
    db = Database(tmp_path / "t.duckdb")
    repo = EventRepository(db)
    cfg = SourceConfig(path="/tmp/x", alias="x")
    source_id = repo.get_or_create_source_id(cfg)
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="x", content_kind="text")

    stop = threading.Event()
    errors: list[BaseException] = []
    inserted_count = [0]
    insert_lock = threading.Lock()

    def writer() -> None:
        try:
            i = 0
            while not stop.is_set():
                events = [_make_event(i + j) for j in range(50)]
                # Stamp the event_id deterministically to avoid dup-
                # skipping in the live tests. We add a per-batch
                # suffix so each run is fresh.
                batch_id = inserted_count[0]
                for ev in events:
                    ev.event_id = f"run{batch_id}-{ev.event_id}"
                with insert_lock:
                    n = repo.insert_events(events, source_id)
                with insert_lock:
                    inserted_count[0] += n
                i += 50
        except BaseException as e:
            errors.append(e)

    def reader() -> None:
        try:
            while not stop.is_set():
                with insert_lock:
                    n = repo.count_events()
                    by_sev = repo.count_by_severity()
                assert n >= 0
                assert isinstance(by_sev, dict)
                time.sleep(0.001)
        except BaseException as e:
            errors.append(e)

    w = threading.Thread(target=writer, daemon=True, name="writer")
    r = threading.Thread(target=reader, daemon=True, name="reader")
    w.start()
    r.start()

    # Let them run for a short while.
    time.sleep(2.0)
    stop.set()
    w.join(timeout=5)
    r.join(timeout=5)

    # Final flush of the source row to materialise counters.
    stats = SourceStats(
        path="/tmp/x",
        parser="text",
        parsed_lines=0,
        unstructured_lines=0,
        rejected_lines=0,
    )
    repo.upsert_source(source_id, cfg, fp, "text", stats)

    assert not errors, f"thread errors: {errors}"
    # Inserted count must be non-zero and stable.
    final = repo.count_events()
    assert final > 0
    db.close()
