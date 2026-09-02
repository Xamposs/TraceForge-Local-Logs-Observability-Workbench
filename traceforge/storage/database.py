"""DuckDB connection management for TraceForge.

We use one ``Database`` object per workspace. The on-disk file lives under
the workspace directory; no global state is shared between workspaces.

**Concurrency model.** All public methods hold an internal re-entrant
lock for the entire *execute* + *fetch* sequence so that one writer
and one reader can coexist on the same connection without corrupting
result sets. Direct access to the underlying :class:`duckdb.DuckDBPyConnection`
via :attr:`raw` is reserved for tightly-controlled internal operations
(typically: Polars registration and bulk inserts performed under a
single :func:`with self._lock` block).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

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
        """The raw DuckDB connection. Use :meth:`execute` / :meth:`fetchone`
        / :meth:`fetchall` whenever possible so the internal lock covers
        the entire execute+fetch sequence.
        """
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
        """Execute a statement and return its result relation.

        The lock is held only for the ``execute`` call; consumers should
        fetch results immediately. For long-running readers prefer
        :meth:`fetchall` / :meth:`fetchone`, which keep the lock for the
        whole execute+fetch.
        """
        with self._lock:
            if params is None:
                return self._con.execute(sql)
            return self._con.execute(sql, params)

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> tuple | None:
        with self._lock:
            rel = self._con.execute(sql, list(params) if params else [])
            return rel.fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        with self._lock:
            rel = self._con.execute(sql, list(params) if params else [])
            return list(rel.fetchall())

    def executemany(self, sql: str, seq: Iterable[tuple]) -> None:
        with self._lock:
            self._con.executemany(sql, list(seq))

    def execute_write(self, sql: str, params: Sequence[Any] | None = None) -> None:
        """Execute a write statement (INSERT/UPDATE/DELETE) under the lock.

        Faster than :meth:`execute` for write paths because it does not
        materialise a result set.
        """
        with self._lock:
            if params is None:
                self._con.execute(sql)
            else:
                self._con.execute(sql, list(params))

    def register_polars(self, name: str, frame) -> None:
        """Register a Polars DataFrame as a temporary view for bulk insert.

        The caller is expected to perform the INSERT (and the
        unregister) within the *same* lock acquisition. Use
        :meth:`bulk_insert_polars` for a safe wrapper.
        """
        with self._lock:
            self._con.register(name, frame)

    def bulk_insert_polars(
        self,
        name: str,
        frame,
        target_table: str,
        *,
        insert_sql: str | None = None,
    ) -> None:
        """Atomically register, insert, and unregister a Polars frame."""
        sql = insert_sql or f"INSERT INTO {target_table} BY NAME SELECT * FROM {name}"
        with self._lock:
            self._con.register(name, frame)
            try:
                self._con.execute(sql)
            finally:
                with suppress(duckdb.Error):
                    self._con.unregister(name)

    def close(self) -> None:
        with self._lock, suppress(duckdb.Error):
            self._con.close()

    def reset(self) -> None:
        """Delete all data and recreate the schema. For tests and clean sessions."""
        with self._lock:
            drop_all(self._con)
            ensure_schema(self._con)
