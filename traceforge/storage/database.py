"""DuckDB connection management for TraceForge.

We use one ``Database`` object per workspace. The on-disk file lives under
the workspace directory; no global state is shared between workspaces.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import duckdb

from traceforge.storage.schema import drop_all, ensure_schema


class Database:
    """A single workspace DuckDB connection.

    Connections are guarded by a re-entrant lock so that the live tailer
    can safely insert events while the UI is querying.
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._read_only = read_only
        self._lock = threading.RLock()
        if read_only:
            self._con = duckdb.connect(str(self._path), read_only=True)
        else:
            self._con = duckdb.connect(str(self._path))
        if not read_only:
            ensure_schema(self._con)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def raw(self) -> duckdb.DuckDBPyConnection:
        return self._con

    @contextmanager
    def tx(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Run a block inside a transaction. Re-entrant safe."""
        with self._lock:
            self._con.execute("BEGIN")
            try:
                yield self._con
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: list | tuple | None = None) -> duckdb.DuckDBPyRelation:
        with self._lock:
            if params is None:
                return self._con.execute(sql)
            return self._con.execute(sql, params)

    def executemany(self, sql: str, seq: list[tuple]) -> None:
        with self._lock:
            self._con.executemany(sql, seq)

    def register_polars(self, name: str, frame) -> None:
        """Register a Polars DataFrame as a temporary view for bulk insert."""
        with self._lock:
            self._con.register(name, frame)

    def close(self) -> None:
        with self._lock, suppress(duckdb.Error):
            self._con.close()

    def reset(self) -> None:
        """Delete all data and recreate the schema. For tests and clean sessions."""
        with self._lock:
            drop_all(self._con)
            ensure_schema(self._con)
