"""Regression tests for the continuous-stream multiline parser."""

from __future__ import annotations

from traceforge.ingestion.pipeline import ingest_file
from traceforge.parsers import ParserContext
from traceforge.parsers.base import SourceLine
from traceforge.parsers.text_parser import CommonTextParser


def _sl(text: str, byte_offset: int = 0, line_number: int = 1) -> SourceLine:
    return SourceLine(
        byte_offset=byte_offset,
        line_number=line_number,
        text=text,
        raw_bytes=text.encode("utf-8"),
    )


def test_multiline_stack_trace_folds_into_one_event() -> None:
    """The spec regression: a 4-line stack trace plus a follow-up line
    must produce exactly two events, with the traceback attached to the
    primary line."""
    parser = CommonTextParser()
    lines = [
        _sl("2026-09-01 12:00:00 ERROR [api] request failed", byte_offset=0, line_number=1),
        _sl("Traceback (most recent call last):", byte_offset=40, line_number=2),
        _sl('  File "service.py", line 20, in run', byte_offset=80, line_number=3),
        _sl('    raise ValueError("bad")', byte_offset=120, line_number=4),
        _sl("ValueError: bad", byte_offset=145, line_number=5),
        _sl("2026-09-01 12:00:01 INFO [api] recovered", byte_offset=160, line_number=6),
    ]
    recs = list(parser.parse(lines, ParserContext(source_path="x", source_alias="x")))
    assert len(recs) == 2
    # First event: the error, containing the traceback.
    err = recs[0].event
    assert err.severity == "ERROR"
    assert err.service == "api"
    assert "Traceback" in err.raw_text
    assert "ValueError: bad" in err.raw_text
    assert "request failed" in err.raw_text
    assert err.byte_offset == 0
    assert err.line_number == 1
    assert recs[0].unstructured is False
    # Second event: the recovery.
    info = recs[1].event
    assert info.severity == "INFO"
    assert info.byte_offset == 160
    assert info.line_number == 6


def test_multiline_continuation_line_attribution() -> None:
    """A continuation line should be attributed to the primary line's
    position, not the continuation's own offset."""
    parser = CommonTextParser()
    lines = [
        _sl("2026-09-01 12:00:00 ERROR boom", byte_offset=0, line_number=1),
        _sl("more details on next line", byte_offset=33, line_number=2),
    ]
    recs = list(parser.parse(lines, ParserContext(source_path="x", source_alias="x")))
    assert len(recs) == 1
    assert recs[0].event.byte_offset == 0
    assert recs[0].event.line_number == 1
    # The continuation line is folded into the message.
    assert "more details on next line" in recs[0].event.raw_text


def test_continuation_after_unmatched_primary_is_unstructured() -> None:
    """An unmatched primary that collects continuations should still be
    flagged as unstructured."""
    parser = CommonTextParser()
    lines = [
        _sl("just a plain line", byte_offset=0, line_number=1),
        _sl("a continuation", byte_offset=20, line_number=2),
    ]
    recs = list(parser.parse(lines, ParserContext(source_path="x", source_alias="x")))
    assert len(recs) == 1
    assert recs[0].event.severity == "UNKNOWN"
    assert recs[0].unstructured is True


def test_end_to_end_multiline_via_ingest_file(tmp_path) -> None:
    """End-to-end: write a log file with a stack trace, ingest it,
    confirm the traceback landed in a single event with the correct
    primary-line attribution."""
    log = tmp_path / "stack.log"
    log.write_text(
        "2026-09-01 12:00:00 ERROR [api] request failed\n"
        "Traceback (most recent call last):\n"
        '  File "service.py", line 20, in run\n'
        '    raise ValueError("bad")\n'
        "ValueError: bad\n"
        "2026-09-01 12:00:01 INFO [api] recovered\n"
    )
    from traceforge.models.sources import SourceConfig
    from traceforge.storage import Database, EventRepository

    db_path = tmp_path / "t.duckdb"
    db = Database(db_path)
    cfg = SourceConfig(path=str(log), alias="stack")
    result = ingest_file(db, cfg)
    repo = EventRepository(db)
    assert repo.count_events() == 2
    rows = db.execute(
        "SELECT severity, message, raw_text, line_number FROM events ORDER BY line_number"
    ).fetchall()
    assert rows[0][0] == "ERROR"
    assert "Traceback" in rows[0][2]
    assert "ValueError: bad" in rows[0][2]
    assert rows[0][3] == 1  # attributed to primary line
    assert rows[1][0] == "INFO"
    db.close()
