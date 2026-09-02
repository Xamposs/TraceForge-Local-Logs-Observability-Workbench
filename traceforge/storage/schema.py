"""DuckDB schema for TraceForge.

Two tables are used:

* ``events`` — the canonical normalized event table.
* ``sources`` — source metadata (fingerprint, parser, last ingest, stats).

The schema is intentionally narrow: canonical fields are typed; arbitrary
attributes are stored as a JSON string in ``attributes_json``.

Schema versions
----------------
* v1 (initial): ``sources.parse_errors`` was overloaded to also store
  ``parsed_events`` count. Stats columns ``first_event_at`` and
  ``last_event_at`` did not exist.
* v2 (current): the stats columns are first-class:
  ``first_event_at``, ``last_event_at``, ``parsed_events``,
  ``inserted_events``. The legacy ``parse_errors`` column is read as a
  fallback for the parsed-events count when the new column is NULL.

On startup, :func:`ensure_schema` performs an idempotent migration: the
table is created with the v2 columns, then any v1-only database is
upgraded in place. Existing user data is never destroyed.
"""

from __future__ import annotations

import contextlib

import duckdb

SCHEMA_VERSION = 2


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
    first_event_at  TIMESTAMPTZ,
    last_event_at   TIMESTAMPTZ,
    last_byte_offset BIGINT NOT NULL DEFAULT 0,
    total_events    BIGINT NOT NULL DEFAULT 0,
    parsed_events   BIGINT NOT NULL DEFAULT 0,
    inserted_events BIGINT NOT NULL DEFAULT 0,
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


# Columns added in v2. Idempotent ALTERs handle v1 -> v2 migration.
_V2_ALTERS = (
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS first_event_at TIMESTAMPTZ",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS parsed_events BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE sources ADD COLUMN IF NOT EXISTS inserted_events BIGINT NOT NULL DEFAULT 0",
)


def _column_exists(con: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    rel = con.execute(
        "SELECT 1 FROM information_schema.columns " "WHERE table_name = ? AND column_name = ?",
        [table, column],
    )
    return rel.fetchone() is not None


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create tables / indexes, then migrate v1 -> v2 if needed."""
    con.execute(SCHEMA_SQL)
    # v1 -> v2 migration. ``IF NOT EXISTS`` makes each ALTER safe to
    # re-run on a fresh v2 schema.
    for alter in _V2_ALTERS:
        with contextlib.suppress(duckdb.Error):
            con.execute(alter)
    con.execute("PRAGMA threads=4")


def drop_all(con: duckdb.DuckDBPyConnection) -> None:
    """Drop all TraceForge tables. Used for tests / workspace reset."""
    for tbl in ("events", "sources"):
        with contextlib.suppress(duckdb.Error):
            con.execute(f"DROP TABLE IF EXISTS {tbl}")


__all__ = ["SCHEMA_VERSION", "ensure_schema", "drop_all"]
