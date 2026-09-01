"""Ingestion pipeline.

Streams a source file through:

1. line reader (bounded memory)
2. format-aware parser
3. normalized events
4. batched DuckDB inserts (via :class:`EventRepository`)

The pipeline supports cancellation via a :class:`threading.Event` and emits
progress information through a simple :class:`IngestionProgress` object.

For parsers that can stream line-by-line (JSONL, common text, Apache, Nginx,
custom regex) we read the file iteratively and parse each line on the fly.
For parsers that need the full document (CSV, JSON array) we collect the
lines first. In both cases the events are accumulated in a batch and
flushed to DuckDB in a single ``executemany`` per batch — this is many
times faster than one insert per line on large datasets.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from traceforge.ingestion.fingerprint import file_fingerprint
from traceforge.ingestion.reader import iter_lines
from traceforge.models.events import SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.parsers import DEFAULT_REGISTRY, Parser, ParserContext
from traceforge.storage import Database, EventRepository


@dataclass
class IngestionProgress:
    source_path: str
    bytes_read: int = 0
    bytes_total: int = 0
    events_parsed: int = 0
    events_inserted: int = 0
    unstructured_lines: int = 0
    rejected_lines: int = 0
    elapsed_s: float = 0.0
    rate_events_per_s: float = 0.0
    cancelled: bool = False
    done: bool = False
    error: str | None = None


@dataclass
class IngestionResult:
    config: SourceConfig
    parser_name: str
    stats: SourceStats
    progress: IngestionProgress


ProgressCallback = Callable[[IngestionProgress], None]


class CancellationToken:
    def __init__(self) -> None:
        self._evt = threading.Event()

    def cancel(self) -> None:
        self._evt.set()

    @property
    def cancelled(self) -> bool:
        return self._evt.is_set()


def _make_event(parser: Parser, context: ParserContext, line: str) -> list:
    """Parse a single line and return a list of records (may be empty)."""
    try:
        return [r for r in parser.parse([line], context) if r is not None]
    except Exception:
        return []


def _stamp_event(ev, raw_bytes: bytes, sample_hash: str) -> None:
    from traceforge.ingestion.ids import stamp_event_id

    stamp_event_id(ev, raw_bytes, sample_hash)


def ingest_file(
    db: Database,
    cfg: SourceConfig,
    *,
    parser_override: str | None = None,
    custom_regex: str | None = None,
    custom_field_map: dict[str, str] | None = None,
    batch_size: int = 5000,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
    on_events: Callable[[list], None] | None = None,
    start_offset: int = 0,
    source_id: int | None = None,
) -> IngestionResult:
    """Ingest a single source into the database."""
    repo = EventRepository(db)
    if source_id is None:
        source_id = repo.next_source_id()
    path = cfg.path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    bytes_total = p.stat().st_size
    fp = file_fingerprint(path)
    sample = fp.sample_hash

    registry = DEFAULT_REGISTRY
    sample_lines: list[str] = []
    if parser_override is None and start_offset == 0:
        from traceforge.parsers.registry import read_sample_lines

        with contextlib.suppress(Exception):
            sample_lines = read_sample_lines(path, max_bytes=64 * 1024)
    detection = registry.detect(
        sample_lines,
        override=parser_override,
        path_hint=path,
    )
    parser: Parser = detection.parser
    if parser.name == "custom_regex":
        parser = registry.by_name("custom_regex") or parser

    context = ParserContext(
        source_path=str(p.resolve()),
        source_alias=cfg.alias or str(p),
        fingerprint_sample=sample,
        custom_regex=custom_regex,
        custom_field_map=custom_field_map or {},
    )
    progress = IngestionProgress(
        source_path=str(p.resolve()),
        bytes_total=bytes_total,
    )
    start_time = time.perf_counter()
    stats = SourceStats(path=str(p.resolve()), parser=parser.name)
    batch: list = []
    cancel_event = cancel or CancellationToken()
    now_seed = datetime.now(tz=UTC)

    def flush_batch() -> None:
        nonlocal batch
        if not batch:
            return
        inserted = repo.insert_events(batch, source_id)
        progress.events_inserted += inserted
        if on_events is not None:
            with contextlib.suppress(Exception):
                on_events(batch)
        batch = []

    try:
        if parser.name in ("jsonl", "text", "apache", "nginx", "custom_regex"):
            # Streaming path: one line at a time.
            for byte_off, line, line_no in iter_lines(path, start_offset=start_offset):
                if cancel_event.cancelled:
                    progress.cancelled = True
                    break
                progress.bytes_read = max(
                    progress.bytes_read, byte_off + len(line.encode("utf-8", errors="replace"))
                )
                stats.total_lines += 1
                if not line:
                    continue
                records = _make_event(parser, context, line)
                if not records:
                    stats.rejected_lines += 1
                    continue
                for rec in records:
                    ev = rec.event
                    if ev.timestamp:
                        if stats.first_event_at is None or ev.timestamp < stats.first_event_at:
                            stats.first_event_at = ev.timestamp
                        if stats.last_event_at is None or ev.timestamp > stats.last_event_at:
                            stats.last_event_at = ev.timestamp
                    ev.byte_offset = byte_off
                    ev.line_number = line_no
                    batch.append(ev)
                    progress.events_parsed += 1
                    if rec.unstructured:
                        progress.unstructured_lines += 1
                        stats.unstructured_lines += 1
                if len(batch) >= batch_size:
                    flush_batch()
        else:
            # Whole-document path (CSV, JSON array).
            progress.bytes_read = bytes_total
            all_lines = list(iter_lines(path, start_offset=start_offset))
            stats.total_lines = len(all_lines)
            try:
                records = list(parser.parse([l[1] for l in all_lines], context))
            except Exception as e:
                stats.parse_errors.append(f"parser: {e}")
                stats.rejected_lines += len(all_lines)
                records = []
            for rec, (byte_off, _, line_no) in zip(records, all_lines, strict=False):
                if rec is None:
                    continue
                if cancel_event.cancelled:
                    progress.cancelled = True
                    break
                ev = rec.event
                if ev.timestamp:
                    if stats.first_event_at is None or ev.timestamp < stats.first_event_at:
                        stats.first_event_at = ev.timestamp
                    if stats.last_event_at is None or ev.timestamp > stats.last_event_at:
                        stats.last_event_at = ev.timestamp
                ev.byte_offset = byte_off
                ev.line_number = line_no
                batch.append(ev)
                progress.events_parsed += 1
                if rec.unstructured:
                    progress.unstructured_lines += 1
                    stats.unstructured_lines += 1
                if len(batch) >= batch_size:
                    flush_batch()
        flush_batch()
    except Exception as e:
        progress.error = str(e)
        raise
    finally:
        progress.elapsed_s = time.perf_counter() - start_time
        if progress.elapsed_s > 0:
            progress.rate_events_per_s = progress.events_parsed / progress.elapsed_s
        progress.done = True
        stats.parsed_lines = progress.events_parsed
        with contextlib.suppress(Exception):
            repo.upsert_source(source_id, cfg, fp, parser.name, stats)
        if on_progress is not None:
            with contextlib.suppress(Exception):
                on_progress(progress)

    return IngestionResult(
        config=cfg,
        parser_name=parser.name,
        stats=stats,
        progress=progress,
    )
