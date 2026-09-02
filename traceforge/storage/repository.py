"""Repository — typed access to the events / sources tables.

All SQL values from the outside world (log content, query inputs) are
bound as parameters. Table/column identifiers used by callers are
explicitly whitelisted through :data:`EVENT_FIELDS` and :data:`SOURCE_FIELDS`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import duckdb

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.storage.database import Database

# Canonical event field names that may be referenced in user-facing
# expressions (filters, sorts, grouping). Anything outside this set is
# rejected by the query compiler.
EVENT_FIELDS: tuple[str, ...] = (
    "event_id",
    "timestamp",
    "ingested_at",
    "severity",
    "message",
    "source",
    "source_path",
    "line_number",
    "byte_offset",
    "service",
    "logger",
    "host",
    "process",
    "thread",
    "trace_id",
    "span_id",
    "parent_span_id",
    "request_id",
    "session_id",
    "duration_ms",
    "status_code",
    "exception_type",
    "raw_format",
)

# Friendly aliases accepted in TFQL: map external name -> SQL column.
EVENT_FIELD_ALIASES: dict[str, str] = {
    "level": "severity",
    "ts": "timestamp",
    "time": "timestamp",
    "@timestamp": "timestamp",
    "service.name": "service",
    "traceId": "trace_id",
    "spanId": "span_id",
    "requestId": "request_id",
    "sessionId": "session_id",
    "duration": "duration_ms",
    "latency_ms": "duration_ms",
    "status": "status_code",
    "code": "status_code",
    "raw": "raw_text",
}

# Fields available in the SELECT projection with display names.
EVENT_DISPLAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("timestamp", "Timestamp"),
    ("severity", "Level"),
    ("service", "Service"),
    ("message", "Message"),
    ("source_alias", "Source"),
    ("trace_id", "Trace ID"),
    ("request_id", "Request ID"),
    ("duration_ms", "Duration (ms)"),
    ("status_code", "Status"),
    ("logger", "Logger"),
    ("host", "Host"),
    ("process", "Process"),
    ("thread", "Thread"),
    ("exception_type", "Exception"),
    ("raw_format", "Format"),
    ("line_number", "Line"),
    ("event_id", "Event ID"),
    ("source_path", "Path"),
)


def resolve_event_field(name: str) -> str | None:
    n = name.strip()
    if n in EVENT_FIELDS:
        return n
    if n in EVENT_FIELD_ALIASES:
        return EVENT_FIELD_ALIASES[n]
    return None


_EVENT_INSERT = (
    "INSERT OR IGNORE INTO events ("
    "event_id, source_id, source_alias, source_path, line_number, byte_offset,"
    " raw_format, timestamp, ingested_at, severity, message, raw_text,"
    " service, logger, host, process, thread,"
    " trace_id, span_id, parent_span_id, request_id, session_id,"
    " duration_ms, status_code, exception_type, attributes_json"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_EVENT_INSERT_COLUMNS = (
    "event_id, source_id, source_alias, source_path, line_number, byte_offset,"
    " raw_format, timestamp, ingested_at, severity, message, raw_text,"
    " service, logger, host, process, thread,"
    " trace_id, span_id, parent_span_id, request_id, session_id,"
    " duration_ms, status_code, exception_type, attributes_json"
)


def _event_row(event: LogEvent, source_id: int) -> tuple:
    return (
        event.event_id,
        source_id,
        event.source,
        event.source_path,
        event.line_number,
        event.byte_offset,
        event.raw_format,
        event.timestamp,
        event.ingested_at,
        event.severity,
        event.message,
        event.raw_text,
        event.service,
        event.logger,
        event.host,
        event.process,
        event.thread,
        event.trace_id,
        event.span_id,
        event.parent_span_id,
        event.request_id,
        event.session_id,
        event.duration_ms,
        event.status_code,
        event.exception_type,
        json.dumps(event.attributes, ensure_ascii=False, default=str),
    )


class EventRepository:
    """Typed access to the events table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- sources ----

    def next_source_id(self) -> int:
        rel = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM sources")
        row = rel.fetchone()
        current = int((row[0] if row else 0) or 0)
        return current + 1

    def get_or_create_source_id(self, cfg: SourceConfig) -> int:
        """Return the source_id for the given config's path, creating a
        new source row if necessary.

        Re-ingesting an existing path always returns the same source_id,
        so events from successive runs can never end up under a different
        source row.
        """
        path = cfg.path
        rel = self._db.execute("SELECT id FROM sources WHERE path = ?", [path])
        row = rel.fetchone()
        if row is not None:
            return int(row[0])
        new_id = self.next_source_id()
        # Insert a placeholder source row so that the source_id is
        # materialised immediately. The full stats are written later by
        # ``upsert_source`` once ingestion completes.
        try:
            self._db.execute(
                "INSERT INTO sources (id, path, alias, enabled, parser) " "VALUES (?, ?, ?, ?, ?)",
                [new_id, path, cfg.alias or path, cfg.enabled, "unknown"],
            )
        except duckdb.Error:
            # Lost the race with another ingest; fall back to the
            # existing row's id.
            row = self._db.execute("SELECT id FROM sources WHERE path = ?", [path]).fetchone()
            if row is not None:
                return int(row[0])
            raise
        return new_id

    def upsert_source(
        self,
        source_id: int,
        cfg: SourceConfig,
        fp: SourceFingerprint,
        parser: str,
        stats: SourceStats,
        *,
        run_inserted: int = 0,
        run_parsed: int = 0,
        last_ingested_at: datetime | None = None,
    ) -> None:
        """Insert a new source row or update an existing one.

        All counters are *additive* (a re-ingest accumulates totals).
        """
        self._db.execute(
            "INSERT INTO sources ("
            "id, path, alias, enabled, parser, size_bytes, mtime_ns, sample_hash,"
            " content_kind, last_ingested_at, first_event_at, last_event_at,"
            " last_byte_offset, total_events, parsed_events, inserted_events,"
            " parse_errors, unstructured_lines, rejected_lines"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "alias = excluded.alias, enabled = excluded.enabled, parser = excluded.parser,"
            "size_bytes = excluded.size_bytes, mtime_ns = excluded.mtime_ns,"
            "sample_hash = excluded.sample_hash, content_kind = excluded.content_kind,"
            "last_ingested_at = COALESCE(excluded.last_ingested_at, sources.last_ingested_at),"
            "first_event_at = COALESCE(excluded.first_event_at, sources.first_event_at),"
            "last_event_at = COALESCE(excluded.last_event_at, sources.last_event_at),"
            "last_byte_offset = excluded.last_byte_offset,"
            "total_events = sources.total_events + excluded.parsed_events,"
            "parsed_events = sources.parsed_events + excluded.parsed_events,"
            "inserted_events = sources.inserted_events + excluded.inserted_events,"
            "parse_errors = sources.parse_errors + excluded.parse_errors,"
            "unstructured_lines = sources.unstructured_lines + excluded.unstructured_lines,"
            "rejected_lines = sources.rejected_lines + excluded.rejected_lines",
            [
                source_id,
                cfg.path,
                cfg.alias or cfg.path,
                cfg.enabled,
                parser,
                fp.size,
                fp.mtime_ns,
                fp.sample_hash,
                fp.content_kind,
                last_ingested_at,
                stats.first_event_at,
                stats.last_event_at,
                stats.bytes_read,
                run_parsed,
                run_parsed,
                run_inserted,
                len(stats.parse_errors),
                stats.unstructured_lines,
                stats.rejected_lines,
            ],
        )

    def update_source_progress(
        self,
        source_id: int,
        *,
        last_byte_offset: int | None = None,
        total_events: int | None = None,
        parsed_events: int | None = None,
        inserted_events: int | None = None,
        parse_errors: int | None = None,
        unstructured_lines: int | None = None,
        rejected_lines: int | None = None,
        last_ingested_at: datetime | None = None,
        first_event_at: datetime | None = None,
        last_event_at: datetime | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if last_byte_offset is not None:
            sets.append("last_byte_offset = ?")
            params.append(last_byte_offset)
        if total_events is not None:
            sets.append("total_events = ?")
            params.append(total_events)
        if parsed_events is not None:
            sets.append("parsed_events = ?")
            params.append(parsed_events)
        if inserted_events is not None:
            sets.append("inserted_events = ?")
            params.append(inserted_events)
        if parse_errors is not None:
            sets.append("parse_errors = ?")
            params.append(parse_errors)
        if unstructured_lines is not None:
            sets.append("unstructured_lines = ?")
            params.append(unstructured_lines)
        if rejected_lines is not None:
            sets.append("rejected_lines = ?")
            params.append(rejected_lines)
        if last_ingested_at is not None:
            sets.append("last_ingested_at = ?")
            params.append(last_ingested_at)
        if first_event_at is not None:
            sets.append("first_event_at = ?")
            params.append(first_event_at)
        if last_event_at is not None:
            sets.append("last_event_at = ?")
            params.append(last_event_at)
        if not sets:
            return
        params.append(source_id)
        sql = f"UPDATE sources SET {', '.join(sets)} WHERE id = ?"
        self._db.execute(sql, params)

    def get_source(self, path: str) -> tuple | None:
        rel = self._db.execute(
            "SELECT id, path, alias, enabled, parser, size_bytes, mtime_ns, sample_hash,"
            " content_kind, last_ingested_at, first_event_at, last_event_at,"
            " last_byte_offset, total_events, parsed_events, inserted_events,"
            " parse_errors, unstructured_lines, rejected_lines"
            " FROM sources WHERE path = ?",
            [path],
        )
        return rel.fetchone()

    def list_sources(self) -> list[tuple]:
        rel = self._db.execute(
            "SELECT id, path, alias, enabled, parser, size_bytes, mtime_ns, sample_hash,"
            " content_kind, last_ingested_at, first_event_at, last_event_at,"
            " last_byte_offset, total_events, parsed_events, inserted_events,"
            " parse_errors, unstructured_lines, rejected_lines"
            " FROM sources ORDER BY alias"
        )
        return list(rel.fetchall())

    def set_source_enabled(self, source_id: int, enabled: bool) -> None:
        self._db.execute(
            "UPDATE sources SET enabled = ? WHERE id = ?",
            [enabled, source_id],
        )

    # ---- events ----

    def insert_events(self, events: Sequence[LogEvent], source_id: int) -> int:
        """Insert events; return the number actually inserted (dedup by PK).

        The returned count is the true delta: events whose ``event_id``
        was already present in the table are silently dropped and
        excluded from the result. This is essential for accurate
        progress reporting when re-ingesting or live-tailing.
        """
        if not events:
            return 0
        import polars as pl

        rows: list[dict] = []
        for e in events:
            rows.append(
                {
                    "event_id": e.event_id,
                    "source_id": int(source_id),
                    "source_alias": e.source,
                    "source_path": e.source_path,
                    "line_number": int(e.line_number),
                    "byte_offset": int(e.byte_offset) if e.byte_offset is not None else None,
                    "raw_format": e.raw_format,
                    "timestamp": e.timestamp,
                    "ingested_at": e.ingested_at,
                    "severity": e.severity,
                    "message": e.message,
                    "raw_text": e.raw_text,
                    "service": e.service,
                    "logger": e.logger,
                    "host": e.host,
                    "process": e.process,
                    "thread": e.thread,
                    "trace_id": e.trace_id,
                    "span_id": e.span_id,
                    "parent_span_id": e.parent_span_id,
                    "request_id": e.request_id,
                    "session_id": e.session_id,
                    "duration_ms": e.duration_ms,
                    "status_code": e.status_code,
                    "exception_type": e.exception_type,
                    "attributes_json": json.dumps(e.attributes, ensure_ascii=False, default=str),
                }
            )
        df = pl.DataFrame(rows, infer_schema_length=len(rows) or 1)
        incoming_ids = [r["event_id"] for r in rows]
        before = self._count_event_ids(incoming_ids)
        # Atomic: register + INSERT + unregister under a single lock.
        self._db.bulk_insert_polars(
            "df_bulk",
            df,
            "events",
            insert_sql="INSERT OR IGNORE INTO events BY NAME SELECT * FROM df_bulk",
        )
        after = self._count_event_ids(incoming_ids)
        return max(0, after - before)

    def _count_event_ids(self, ids: Sequence[str]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        row = self._db.fetchone(
            f"SELECT COUNT(*) FROM events WHERE event_id IN ({placeholders})",
            list(ids),
        )
        return int(row[0]) if row else 0

    def count_events(self) -> int:
        row = self._db.fetchone("SELECT COUNT(*) FROM events")
        return int(row[0]) if row else 0

    def count_by_severity(self) -> dict[str, int]:
        rows = self._db.fetchall("SELECT severity, COUNT(*) FROM events GROUP BY severity")
        return {row[0]: int(row[1]) for row in rows}

    def time_range(self) -> tuple[datetime | None, datetime | None]:
        row = self._db.fetchone(
            "SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE timestamp IS NOT NULL"
        )
        return (row[0], row[1]) if row else (None, None)

    def distinct_services(self) -> list[str]:
        rows = self._db.fetchall(
            "SELECT DISTINCT service FROM events WHERE service IS NOT NULL ORDER BY service"
        )
        return [r[0] for r in rows]

    def fetch_event(self, event_id: str) -> tuple | None:
        return self._db.fetchone(
            "SELECT event_id, source_id, source_alias, source_path, line_number,"
            " byte_offset, raw_format, timestamp, ingested_at, severity, message,"
            " raw_text, service, logger, host, process, thread,"
            " trace_id, span_id, parent_span_id, request_id, session_id,"
            " duration_ms, status_code, exception_type, attributes_json"
            " FROM events WHERE event_id = ?",
            [event_id],
        )

    def fetch_by_correlation(
        self,
        *,
        trace_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> list[tuple]:
        clauses: list[str] = []
        params: list[Any] = []
        if trace_id is not None:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if request_id is not None:
            clauses.append("request_id = ?")
            params.append(request_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if not clauses:
            return []
        where = " OR ".join(clauses)
        return self._db.fetchall(
            "SELECT event_id, source_alias, source_path, line_number, timestamp,"
            " severity, message, service, trace_id, span_id, parent_span_id,"
            " request_id, session_id, duration_ms, status_code"
            f" FROM events WHERE {where} ORDER BY timestamp",
            params,
        )

    def event_ids_for_signature(self, signature: str) -> list[str]:
        rows = self._db.fetchall(
            "SELECT event_id FROM events WHERE severity IN ('ERROR','FATAL','CRITICAL')"
            " AND message LIKE ? LIMIT 500",
            [f"%{signature[:60]}%"],
        )
        return [r[0] for r in rows]
