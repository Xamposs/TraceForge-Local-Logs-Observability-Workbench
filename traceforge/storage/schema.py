"""DuckDB schema for TraceForge.

Two layers of tables are used:

* ``events`` — the canonical normalized event table.
* ``sources`` — source metadata (fingerprint, parser, last ingest, status).

Indexes / ordering: DuckDB is a columnar store; we keep a small number of
indexes on the most selective columns (timestamp, severity, source, trace_id,
request_id) to support efficient filtering.

The schema is intentionally narrow: canonical fields are typed; arbitrary
attributes are stored as a JSON string in ``attributes_json``.
"""

from __future__ import annotations

import contextlib

import duckdb

SCHEMA_VERSION = 1


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS sources (
    id              BIGINT PRIMARY KEY,
    path            VARCHAR NOT NULL,
    alias           VARCHAR NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    parser          VARCHAR NOT NULL,
    size_bytes      BIGINT NOT NULL DEFAULT 0,
    mtime_ns        BIGINT NOT NULL DEFAULT 0,
    sample_hash     VARCHAR NOT NULL DEFAULT '',
    content_kind    VARCHAR NOT NULL DEFAULT 'text',
    last_ingested_at TIMESTAMPTZ,
    last_byte_offset BIGINT NOT NULL DEFAULT 0,
    total_events    BIGINT NOT NULL DEFAULT 0,
    parse_errors    BIGINT NOT NULL DEFAULT 0,
    unstructured_lines BIGINT NOT NULL DEFAULT 0,
    rejected_lines  BIGINT NOT NULL DEFAULT 0,
    UNIQUE(path)
);

CREATE TABLE IF NOT EXISTS events (
    event_id        VARCHAR PRIMARY KEY,
    source_id       BIGINT NOT NULL,
    source_alias    VARCHAR NOT NULL,
    source_path     VARCHAR NOT NULL,
    line_number     BIGINT NOT NULL,
    byte_offset     BIGINT,
    raw_format      VARCHAR NOT NULL,
    timestamp       TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ NOT NULL,
    severity        VARCHAR NOT NULL,
    message         VARCHAR NOT NULL,
    raw_text        VARCHAR NOT NULL,
    service         VARCHAR,
    logger          VARCHAR,
    host            VARCHAR,
    process         VARCHAR,
    thread          VARCHAR,
    trace_id        VARCHAR,
    span_id         VARCHAR,
    parent_span_id  VARCHAR,
    request_id      VARCHAR,
    session_id      VARCHAR,
    duration_ms     DOUBLE,
    status_code     INTEGER,
    exception_type  VARCHAR,
    attributes_json VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_id);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(service);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_request ON events(request_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create tables and indexes if they do not exist."""
    con.execute(SCHEMA_SQL)
    con.execute("PRAGMA threads=4")


def drop_all(con: duckdb.DuckDBPyConnection) -> None:
    """Drop all TraceForge tables. Used for tests / workspace reset."""
    for tbl in ("events", "sources"):
        with contextlib.suppress(duckdb.Error):
            con.execute(f"DROP TABLE IF EXISTS {tbl}")
