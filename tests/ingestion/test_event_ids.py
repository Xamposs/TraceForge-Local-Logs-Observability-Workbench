"""Regression tests for :mod:`traceforge.ingestion.ids`.

Covers deterministic event-id composition: same line + same offset =
same id; differing offset or content produce distinct ids; line number is
not consulted when byte offset is known.
"""

from __future__ import annotations

from datetime import UTC, datetime

from traceforge.ingestion.ids import compute_event_id, stamp_event_id
from traceforge.models.events import LogEvent


def test_same_line_same_offset_same_id() -> None:
    a = compute_event_id("/var/log/app.log", b"hello", byte_offset=42)
    b = compute_event_id("/var/log/app.log", b"hello", byte_offset=42)
    assert a == b


def test_different_offset_different_id() -> None:
    a = compute_event_id("/var/log/app.log", b"hello", byte_offset=42)
    b = compute_event_id("/var/log/app.log", b"hello", byte_offset=43)
    assert a != b


def test_different_content_different_id() -> None:
    a = compute_event_id("/var/log/app.log", b"hello", byte_offset=42)
    b = compute_event_id("/var/log/app.log", b"hello!", byte_offset=42)
    assert a != b


def test_different_path_different_id() -> None:
    a = compute_event_id("/var/log/app.log", b"hello", byte_offset=42)
    b = compute_event_id("/var/log/other.log", b"hello", byte_offset=42)
    assert a != b


def test_byte_offset_zero_is_valid() -> None:
    a = compute_event_id("/p", b"x", byte_offset=0)
    # byte_offset=0 must be distinct from line_number=1
    b = compute_event_id("/p", b"x", line_number=1)
    assert a != b


def test_line_number_used_only_when_no_offset() -> None:
    # When byte_offset is None, the line_number participates in the id.
    a = compute_event_id("/p", b"x", line_number=5)
    b = compute_event_id("/p", b"x", line_number=6)
    assert a != b


def test_live_reread_same_byte_offset_keeps_id() -> None:
    """Live tail re-reads the same line; its byte_offset is identical to
    the original ingestion, so the event_id must match even if the line
    number happens to differ between the two passes."""
    a = compute_event_id("/p", b"hello", byte_offset=1024, line_number=1)
    b = compute_event_id("/p", b"hello", byte_offset=1024, line_number=99)
    assert a == b


def test_no_position_fallback() -> None:
    a = compute_event_id("/p", b"x")
    # No explicit position; falls back to a token. Same input -> same id.
    b = compute_event_id("/p", b"x")
    assert a == b
    # Different content still distinct.
    c = compute_event_id("/p", b"y")
    assert a != c


def test_stamp_event_id_sets_byte_offset_when_present() -> None:
    raw = b"some log line"
    ev = LogEvent(
        event_id="",
        source="app",
        source_path="/p",
        line_number=1,
        timestamp=None,
        ingested_at=datetime.now(tz=UTC),
        severity="INFO",
        message="some log line",
        raw_text=raw.decode("utf-8"),
        raw_format="text",
        byte_offset=500,
    )
    stamp_event_id(ev, raw)
    expected = compute_event_id("/p", raw, byte_offset=500)
    assert ev.event_id == expected


def test_stamp_event_id_falls_back_to_line_number() -> None:
    raw = b"x"
    ev = LogEvent(
        event_id="",
        source="app",
        source_path="/p",
        line_number=7,
        timestamp=None,
        ingested_at=datetime.now(tz=UTC),
        severity="INFO",
        message="x",
        raw_text=raw.decode("utf-8"),
        raw_format="text",
        byte_offset=None,
    )
    stamp_event_id(ev, raw)
    expected = compute_event_id("/p", raw, line_number=7)
    assert ev.event_id == expected


def test_path_normalization_collapses_case() -> None:
    a = compute_event_id("C:\\Logs\\App.LOG", b"x", byte_offset=1)
    b = compute_event_id("c:\\logs\\app.log", b"x", byte_offset=1)
    assert a == b
