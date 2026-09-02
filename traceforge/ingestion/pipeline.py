"""Ingestion pipeline.

Streams a source file through:

1. line reader (bounded memory) -> :class:`SourceLine` objects
2. format-aware parser (continuous line stream)
3. normalized events
4. batched DuckDB inserts (via :class:`EventRepository`)

The pipeline supports cancellation via a :class:`threading.Event` and emits
progress information through a simple :class:`IngestionProgress` object.

For parsers that can stream line-by-line (JSONL, common text, Apache,
Nginx, custom regex) the reader feeds one :class:`SourceLine` at a time
into the parser; the parser retains state across the whole stream so
multi-line stack traces assemble correctly. For parsers that need the
full document (CSV, JSON array) the same streaming API is used and the
parser buffers internally.

Events are accumulated in a batch and flushed to DuckDB in a single
``INSERT OR IGNORE`` over a registered Polars DataFrame. The pipeline
returns the *actual* number of inserted (non-duplicate) events.
"""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from traceforge.ingestion.fingerprint import file_fingerprint
from traceforge.ingestion.reader import iter_lines
from traceforge.models.events import SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.parsers import DEFAULT_REGISTRY, Parser, ParserContext
from traceforge.parsers.base import SourceLine
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
    parse_errors: int = 0
    last_ingested_at: datetime | None = None
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    last_byte_offset: int = 0
    elapsed_s: float = 0.0
    rate_events_per_s: float = 0.0
    cancelled: bool = False
    done: bool = False
    error: str | None = None
    parser_diagnostics: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.parser_diagnostics is None:
            self.parser_diagnostics = []


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


def _lines_from_reader(
    path: str | os.PathLike[str],
    start_offset: int = 0,
    buffer_bytes: int = 1 << 20,
    cancel: CancellationToken | None = None,
) -> Iterable[SourceLine]:
    """Convert :func:`iter_lines` output to :class:`SourceLine` objects.

    The reader already supplies exact byte offsets, line numbers, and
    decoded text. We re-encode the text once for ``raw_bytes`` so the
    parsers can stamp event IDs without ambiguity.

    If a ``cancel`` token is supplied, the iterator checks it before
    yielding each line. Parsers are not aware of cancellation; the
    pipeline must check the token between yield points, or the parser
    must yield frequently enough to allow timely cancellation. The
    JSONL/text parsers yield per-line, so the iterator-driven check
    is sufficient.
    """
    for byte_offset, text, line_no in iter_lines(path, start_offset=start_offset, buffer_bytes=buffer_bytes):
        if cancel is not None and cancel.cancelled:
            return
        raw = text.encode("utf-8", errors="replace")
        yield SourceLine(byte_offset=byte_offset, line_number=line_no, text=text, raw_bytes=raw)


def _lines_from_bytes(
    data: bytes,
    start_offset: int = 0,
) -> Iterable[SourceLine]:
    """Yield :class:`SourceLine` objects from an in-memory byte slice.

    Used by the live tailer to ingest newly-appended bytes with the
    correct absolute file byte offsets. Lines are produced via the
    same reader logic as the file-backed path; line numbers start
    at 1 (the caller has already accounted for previous lines via
    ``start_offset``).
    """
    if not data:
        return
    bio = io.BytesIO(data)
    pos = 0
    line_no = 1
    while True:
        line = bio.readline()
        if not line:
            return
        # Compute the absolute byte offset of the first byte of this
        # line. ``pos`` is the position of the next byte to be read.
        line_offset = pos
        text = line.rstrip(b"\n\r")
        try:
            text_str = text.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - extremely defensive
            text_str = text.decode("utf-8", errors="replace")
        yield SourceLine(
            byte_offset=start_offset + line_offset,
            line_number=line_no,
            text=text_str,
            raw_bytes=text,
        )
        pos += len(line)
        line_no += 1


def _record_event(
    ev,
    src: SourceLine,
    progress: IngestionProgress,
    stats: SourceStats,
    record_unstructured: bool,
) -> None:
    """Finalise a parser-produced event with source position and stats."""
    if ev.timestamp:
        if stats.first_event_at is None or ev.timestamp < stats.first_event_at:
            stats.first_event_at = ev.timestamp
        if stats.last_event_at is None or ev.timestamp > stats.last_event_at:
            stats.last_event_at = ev.timestamp
    if ev.byte_offset == 0 and src.byte_offset:
        ev.byte_offset = src.byte_offset
    if ev.line_number == 0 and src.line_number:
        ev.line_number = src.line_number
    progress.last_byte_offset = max(progress.last_byte_offset, ev.byte_offset or 0)
    progress.events_parsed += 1
    if record_unstructured:
        progress.unstructured_lines += 1


def _detect_parser(
    path: str | os.PathLike[str],
    override: str | None,
    start_offset: int,
) -> tuple[Parser, list[str]]:
    """Pick a parser. Returns (parser, sample_lines_used)."""
    sample_lines: list[str] = []
    if override is None and start_offset == 0:
        from traceforge.parsers.registry import read_sample_lines

        with contextlib.suppress(Exception):
            sample_lines = read_sample_lines(path, max_bytes=64 * 1024)
    detection = DEFAULT_REGISTRY.detect(sample_lines, override=override, path_hint=path)
    parser: Parser = detection.parser
    if parser.name == "custom_regex":
        parser = DEFAULT_REGISTRY.by_name("custom_regex") or parser
    return parser, sample_lines


def parse_bytes_to_events(
    db: Database,
    cfg: SourceConfig,
    *,
    data: bytes,
    start_offset: int,
    source_id: int | None,
    parser_override: str | None = None,
    custom_regex: str | None = None,
    custom_field_map: dict[str, str] | None = None,
    cancel: CancellationToken | None = None,
) -> list:
    """Parse a raw byte slice and insert the resulting events.

    The slice is interpreted as the new content appended at
    ``start_offset`` of the source identified by ``cfg.path``. The
    parser is selected using ``cfg``'s path. Returned list contains
    the inserted :class:`LogEvent` objects.
    """
    if not data:
        return []
    from traceforge.ingestion.fingerprint import file_fingerprint
    from traceforge.models.events import SourceStats

    repo = EventRepository(db)
    if source_id is None:
        source_id = repo.get_or_create_source_id(cfg)
    p = Path(cfg.path)
    if not p.exists():
        raise FileNotFoundError(cfg.path)
    fp = file_fingerprint(cfg.path)
    sample = fp.sample_hash
    parser, _ = _detect_parser(cfg.path, parser_override, start_offset)
    context = ParserContext(
        source_path=str(p.resolve()),
        source_alias=cfg.alias or str(p),
        fingerprint_sample=sample,
        custom_regex=custom_regex,
        custom_field_map=custom_field_map or {},
    )
    cancel_event = cancel or CancellationToken()
    events: list = []
    try:
        line_iter = _lines_from_bytes(data, start_offset=start_offset)
        for src in line_iter:
            if cancel_event.cancelled:
                break
            for rec in parser.parse(_one_line_iter(src), context):
                if rec is None or rec.event is None:
                    continue
                ev = rec.event
                events.append(ev)
        if events:
            repo.insert_events(events, source_id)
    finally:
        # Update the source row's stats; we don't have a full
        # IngestionProgress here so we synthesise a minimal one.
        stats = SourceStats(
            path=str(p.resolve()),
            parser=parser.name,
            parsed_lines=len(events),
            unstructured_lines=sum(1 for e in events if e.severity == "UNKNOWN"),
        )
        with contextlib.suppress(Exception):
            repo.upsert_source(
                source_id,
                cfg,
                fp,
                parser.name,
                stats,
                run_inserted=len(events),
                run_parsed=len(events),
                last_ingested_at=datetime.now(tz=UTC),
            )
    return events


def _one_line_iter(src: SourceLine) -> Iterable[SourceLine]:
    """Wrap a single :class:`SourceLine` in an iterable for the parser."""
    yield src


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
    """Ingest a single source into the database.

    The whole source is streamed through the parser as one call so that
    stateful parsers (e.g. :class:`CommonTextParser`) retain their
    multi-line state across the entire file.
    """
    repo = EventRepository(db)
    if source_id is None:
        # Reuse an existing source row for this path, or create one.
        source_id = repo.get_or_create_source_id(cfg)
    path = cfg.path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    bytes_total = p.stat().st_size
    fp = file_fingerprint(path)
    sample = fp.sample_hash

    parser, _ = _detect_parser(path, parser_override, start_offset)

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
        line_iter = _lines_from_reader(path, start_offset=start_offset, cancel=cancel_event)
        try:
            records = list(parser.parse(line_iter, context))
        except Exception as e:
            # An unexpected parser exception is a programming bug,
            # not malformed input. Record it but do not abort.
            progress.parse_errors += 1
            progress.parser_diagnostics.append(f"unexpected parser exception: {type(e).__name__}: {e}")
            records = []
        if cancel_event.cancelled:
            progress.cancelled = True
        # Harvest parser diagnostics that may have been recorded on
        # the context during parsing.
        diag_bucket = getattr(context, "_parser_diagnostics", None)
        if diag_bucket:
            for d in diag_bucket:
                progress.parser_diagnostics.append(str(d))
        last_src: SourceLine | None = None
        # Track the maximum byte_offset seen so ``bytes_read`` is exact.
        max_seen_offset = 0
        line_count = 0
        for rec in records:
            if rec is None:
                continue
            ev = rec.event
            if ev.byte_offset:
                if ev.byte_offset > max_seen_offset:
                    max_seen_offset = ev.byte_offset
            if ev.line_number:
                if ev.line_number > line_count:
                    line_count = ev.line_number
            src_for_event = last_src or SourceLine(
                byte_offset=ev.byte_offset or 0,
                line_number=ev.line_number or 1,
                text="",
                raw_bytes=b"",
            )
            _record_event(ev, src_for_event, progress, stats, rec.unstructured)
            if rec.unstructured:
                stats.unstructured_lines += 1
            batch.append(ev)
            if len(batch) >= batch_size:
                flush_batch()
            if ev.line_number:
                last_src = src_for_event
        stats.total_lines = line_count
        progress.bytes_read = max(progress.bytes_read, max_seen_offset)
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
        # Update source row with the final stats. We do this even on
        # cancellation so the partial progress is recorded.
        with contextlib.suppress(Exception):
            repo.upsert_source(
                source_id,
                cfg,
                fp,
                parser.name,
                stats,
                run_inserted=progress.events_inserted,
                run_parsed=progress.events_parsed,
                last_ingested_at=datetime.now(tz=UTC),
            )
        if on_progress is not None:
            with contextlib.suppress(Exception):
                on_progress(progress)

    return IngestionResult(
        config=cfg,
        parser_name=parser.name,
        stats=stats,
        progress=progress,
    )
