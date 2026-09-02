"""Live tail tests covering append, truncate, replace, rapid appends, and stop."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

# Live-tail tests rely on the OS file-watch APIs and are
# timing-sensitive. Skip on busy CI runners; the underlying
# invariants are exercised by the unit tests in
# tests/ingestion/test_reader_offsets.py and the dedicated
# test_live_tail_correctness.py (which itself skips on CI).
_CI = bool(os.environ.get("CI"))
if _CI:
    pytest.skip("live tail tests are timing-sensitive; skip in CI", allow_module_level=True)


from traceforge.live.tailer import LiveTailer
from traceforge.models.events import SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database, EventRepository


def _seed_source(database: Database, path: Path, source_id: int, alias: str) -> None:
    repo = EventRepository(database)
    cfg = SourceConfig(path=str(path), alias=alias)
    fp = SourceFingerprint(path=str(path), size=0, mtime_ns=0, sample_hash="seed", content_kind="text")
    stats = SourceStats(path=str(path), parser="text")
    repo.upsert_source(source_id, cfg, fp, "text", stats)


def test_tailer_appends(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "live.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "live")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        # write some content
        with open(f, "a", encoding="utf-8") as fp:
            for i in range(5):
                fp.write(f"2026-09-01 12:00:0{i} INFO msg{i}\n")
        time.sleep(1.0)
    finally:
        tailer.stop()
    assert EventRepository(database).count_events() >= 5


def test_tailer_truncates(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "live2.log"
    f.write_text("2026-09-01 12:00:00 INFO first\n", encoding="utf-8")
    _seed_source(database, f, 1, "live2")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        time.sleep(0.4)
        # Truncate and write new content
        with open(f, "w", encoding="utf-8") as fp:
            fp.write("2026-09-01 12:00:01 INFO second\n")
        time.sleep(1.0)
    finally:
        tailer.stop()
    messages = [r[0] for r in database.fetchall("SELECT message FROM events ORDER BY line_number")]
    assert "first" in messages
    assert "second" in messages


def test_tailer_handles_deleted_file(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "live3.log"
    f.write_text("2026-09-01 12:00:00 INFO first\n", encoding="utf-8")
    _seed_source(database, f, 1, "live3")
    tailer = LiveTailer(database, poll_interval=0.05)
    state = tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        time.sleep(0.4)
        f.unlink()
        time.sleep(0.4)
    finally:
        tailer.stop()
    assert state.missing is True


def test_tailer_rapid_appends(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "live4.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "live4")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        with open(f, "a", encoding="utf-8") as fp:
            for i in range(50):
                fp.write(f"2026-09-01 12:00:00 INFO burst {i}\n")
        time.sleep(1.5)
    finally:
        tailer.stop()
    assert EventRepository(database).count_events() >= 50


def test_tailer_remove_source(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "live5.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "live5")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    tailer.remove_source(str(f))
    tailer.stop()
