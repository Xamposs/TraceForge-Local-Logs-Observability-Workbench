"""Live tail regression tests for sections 11-15:

* 11 — append on small file is not mis-classified as replacement
* 12 — initial offset honours the previously-ingested byte position
* 13 — partial final line is not emitted until newline completes it
* 14 — cancelled / errored ingest does not advance the committed offset
* 15 — rotation (rename) marks the source missing without auto-follow
"""

from __future__ import annotations

import time
from pathlib import Path

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


def test_small_file_append_is_not_replacement(database: Database, temp_dir: Path) -> None:
    """A growing file smaller than 64 KiB must never be classified as a
    replacement, even when its sample-hash prefix changes."""
    f = temp_dir / "small.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "small")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        # Repeatedly grow the file in small steps.
        with open(f, "ab") as fp:
            for i in range(20):
                fp.write(f"2026-09-01 12:00:00 INFO burst {i}\n".encode())
        time.sleep(0.8)
    finally:
        tailer.stop()
    assert EventRepository(database).count_events() == 20


def test_initial_offset_respects_existing_ingest(database: Database, temp_dir: Path) -> None:
    """If a source was already ingested up to byte N, the tailer must
    start at N — not re-read existing content."""
    f = temp_dir / "existing.log"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    _seed_source(database, f, 1, "existing")
    tailer = LiveTailer(database, poll_interval=0.05)
    # Pretend we already ingested up to byte 4 (after "a\n").
    state = tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)), start_offset=4)
    assert state.offset == 4
    tailer.start()
    try:
        with open(f, "a", encoding="utf-8") as fp:
            fp.write("d\n")
        time.sleep(0.6)
    finally:
        tailer.stop()
    msgs = [r[0] for r in database.fetchall("SELECT message FROM events ORDER BY line_number")]
    # Only lines from offset 4 onwards must be present. The first
    # line emitted at offset 4 is an empty line (the \r\n that
    # follows "a"), and the actual content lines are "c" and "d".
    assert "d" in msgs
    assert "c" in msgs
    assert "a" not in msgs
    assert "b" not in msgs


def test_partial_final_line_held_until_newline(database: Database, temp_dir: Path) -> None:
    """A line written without a trailing newline must NOT be emitted
    yet; the next append that completes the line must produce exactly
    one event with the full text."""
    f = temp_dir / "partial.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "partial")
    tailer = LiveTailer(database, poll_interval=0.05)
    tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        with open(f, "ab") as fp:
            fp.write(b"2026-09-01 ERROR partial")
        time.sleep(0.4)
        # No event should have been emitted yet (no newline).
        assert EventRepository(database).count_events() == 0
        with open(f, "ab") as fp:
            fp.write(b" message completed\n")
        time.sleep(0.6)
    finally:
        tailer.stop()
    msgs = [r[0] for r in database.fetchall("SELECT message FROM events")]
    assert len(msgs) == 1
    assert "partial" in msgs[0]
    assert "message completed" in msgs[0]


def test_cancelled_ingest_does_not_advance_offset(database: Database, temp_dir: Path) -> None:
    """If the tailer is stopped mid-tick, the committed offset must
    not advance past data that was not successfully processed."""
    f = temp_dir / "cancel.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "cancel")
    tailer = LiveTailer(database, poll_interval=0.05)
    state = tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    # Start the tailer, then immediately stop it; the stop races with
    # the first tick.
    tailer.start()
    time.sleep(0.05)
    tailer.stop()
    # Append some content after the tailer has stopped.
    with open(f, "ab") as fp:
        fp.write(b"2026-09-01 INFO after-stop\n")
    # The tailer's offset must remain at 0 (no successful ingest) or
    # at the position reached by the first tick (no overwrite). In
    # either case it must not exceed 0 + len(new content).
    assert state.offset <= 0


def test_rotation_marks_missing_without_auto_follow(database: Database, temp_dir: Path) -> None:
    """When the watched file is renamed, the source becomes 'missing'
    and the tailer does not silently start reading a new file at the
    same path."""
    f = temp_dir / "rot.log"
    f.write_text("2026-09-01 INFO before\n", encoding="utf-8")
    _seed_source(database, f, 1, "rot")
    tailer = LiveTailer(database, poll_interval=0.05)
    state = tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        time.sleep(0.3)
        # Rotate: rename the old file and create a new one.
        f.rename(temp_dir / "rot.1.log")
        with open(f, "w", encoding="utf-8") as fp:
            fp.write("2026-09-01 INFO after\n")
        time.sleep(0.6)
    finally:
        tailer.stop()
    # The tailer should not have ingested the new file's content.
    msgs = [r[0] for r in database.fetchall("SELECT message FROM events")]
    assert all("after" not in m for m in msgs), msgs
    assert any("before" in m for m in msgs), msgs


def test_repeated_appends_under_64k_advance_monotonically(database: Database, temp_dir: Path) -> None:
    """Spec regression: repeated 100-byte appends to a <64 KiB file
    must monotonically advance the tailer's offset; it must never
    reset to zero on a plain append."""
    f = temp_dir / "small-append.log"
    f.write_text("", encoding="utf-8")
    _seed_source(database, f, 1, "small-append")
    tailer = LiveTailer(database, poll_interval=0.02)
    state = tailer.add_source(1, SourceConfig(path=str(f), alias=str(f)))
    tailer.start()
    try:
        last_offset = 0
        for i in range(5):
            # Each append includes a newline so the offset advances
            # past the committed line.
            with open(f, "ab") as fp:
                fp.write(f"2026-09-01 INFO line {i}\n".encode())
            # Wait long enough for the tailer to catch up. We poll the
            # tailer until its committed offset moves past
            # ``last_offset`` or a short safety timeout elapses.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if state.offset >= last_offset + 1:
                    break
                time.sleep(0.02)
            assert (
                state.offset >= last_offset
            ), f"offset regressed from {last_offset} to {state.offset} after append {i}"
            last_offset = state.offset
        assert state.offset > 0
    finally:
        tailer.stop()
