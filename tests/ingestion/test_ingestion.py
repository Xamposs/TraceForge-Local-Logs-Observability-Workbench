"""Ingestion pipeline tests."""

from __future__ import annotations

from pathlib import Path

from traceforge.ingestion.pipeline import CancellationToken, ingest_file
from traceforge.ingestion.reader import iter_lines
from traceforge.models.sources import SourceConfig
from traceforge.parsers.registry import read_sample_lines
from traceforge.storage import Database, EventRepository


def test_iter_lines_streams_file(temp_dir: Path) -> None:
    f = temp_dir / "a.log"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    lines = list(iter_lines(f))
    assert [t[1] for t in lines] == ["a", "b", "c"]
    assert lines[0][2] == 1


def test_iter_lines_truncates_huge_line(temp_dir: Path) -> None:
    f = temp_dir / "big.log"
    f.write_bytes(b"x" * (2_000_000) + b"\n")
    lines = list(iter_lines(f, max_line_bytes=100))
    assert lines[0][1].endswith("...[truncated]")


def test_iter_lines_respects_start_offset(temp_dir: Path) -> None:
    f = temp_dir / "b.log"
    f.write_bytes(b"a\nb\nc\n")
    lines = list(iter_lines(f, start_offset=2))  # skip 'a\n'
    assert [t[1] for t in lines] == ["b", "c"]


def test_ingest_jsonl(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.jsonl"
    f.write_text(
        '{"timestamp":"2026-09-01T12:00:00Z","level":"INFO","message":"a","service":"api"}\n'
        '{"timestamp":"2026-09-01T12:00:01Z","level":"ERROR","message":"b","service":"api"}\n',
        encoding="utf-8",
    )
    cfg = SourceConfig(path=str(f), alias=f.name)
    res = ingest_file(database, cfg)
    assert res.progress.events_parsed == 2
    assert res.parser_name == "jsonl"
    assert database.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_ingest_text_database(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.log"
    f.write_text(
        "2026-09-01 12:00:00 INFO [api] hello\n" "2026-09-01 12:00:01 ERROR [api] world\n",
        encoding="utf-8",
    )
    cfg = SourceConfig(path=str(f), alias=f.name)
    res = ingest_file(database, cfg)
    assert res.progress.events_parsed == 2
    rel = database.execute("SELECT severity FROM events ORDER BY line_number")
    assert [r[0] for r in rel.fetchall()] == ["INFO", "ERROR"]


def test_ingest_csv(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.csv"
    f.write_text("timestamp,level,message\n2026-09-01,INFO,hi\n2026-09-01,ERROR,oh\n", encoding="utf-8")
    cfg = SourceConfig(path=str(f), alias=f.name)
    res = ingest_file(database, cfg)
    assert res.progress.events_parsed == 2


def test_ingest_is_incremental(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.log"
    f.write_text("2026-09-01 12:00:00 INFO a\n", encoding="utf-8")
    cfg = SourceConfig(path=str(f), alias=f.name)
    res1 = ingest_file(database, cfg)
    assert res1.progress.events_parsed == 1
    # Append a new line and ingest again from offset=0; the first line is deduped.
    with open(f, "a", encoding="utf-8") as fp:
        fp.write("2026-09-01 12:00:01 INFO b\n")
    ingest_file(database, cfg)
    # One of the lines is new, one is a duplicate.
    assert EventRepository(database).count_events() == 2


def test_ingest_cancellation(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.log"
    lines = "\n".join(f"2026-09-01 12:00:0{i%10} INFO msg{i}" for i in range(1000)) + "\n"
    f.write_text(lines, encoding="utf-8")
    cancel = CancellationToken()
    cancel.cancel()  # cancel before starting
    cfg = SourceConfig(path=str(f), alias=f.name)
    res = ingest_file(database, cfg, cancel=cancel)
    assert res.progress.cancelled is True


def test_ingest_does_not_modify_source(database: Database, temp_dir: Path) -> None:
    f = temp_dir / "events.log"
    f.write_text("2026-09-01 12:00:00 INFO hello\n", encoding="utf-8")
    expected = f.read_bytes()
    cfg = SourceConfig(path=str(f), alias=f.name)
    ingest_file(database, cfg)
    assert f.read_bytes() == expected


def test_read_sample_lines_does_not_read_full_file(temp_dir: Path) -> None:
    f = temp_dir / "huge.log"
    f.write_bytes(b"a\n" * 100_000)
    sample = read_sample_lines(f, max_bytes=2048)
    assert len(sample) < 100_000
