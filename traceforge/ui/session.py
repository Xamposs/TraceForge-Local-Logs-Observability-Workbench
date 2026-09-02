"""TraceForge application session — the GUI's central state.

This is intentionally Qt-free at the data layer. The UI layer translates
between Qt and this class; the CLI and tests can use it directly.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traceforge.app_paths import AppPaths
from traceforge.ingestion.pipeline import (
    CancellationToken,
    IngestionProgress,
    ingest_file,
)
from traceforge.models.sources import SourceConfig
from traceforge.models.workspace import (
    SavedQuery,
    Workspace,
)
from traceforge.query import QueryError, QueryResult
from traceforge.query import execute as tfql_execute
from traceforge.rules import default_rules, run_all
from traceforge.storage import Database, EventRepository
from traceforge.workspace_io import load_workspace, new_workspace, save_workspace

ProgressCallback = Callable[[IngestionProgress], None]
QueryCallback = Callable[[QueryResult], None]


@dataclass
class SourceIngestionStatus:
    path: str
    alias: str
    parser: str
    total_events: int
    bytes_read: int
    bytes_total: int
    parsed_lines: int
    unstructured_lines: int
    rejected_lines: int
    first_event_at: datetime | None
    last_event_at: datetime | None


@dataclass
class WorkspaceSummary:
    name: str
    db_path: Path
    sources: list[SourceIngestionStatus]
    event_count: int
    severity_counts: dict[str, int]
    time_range: tuple[datetime | None, datetime | None]
    workspace_path: Path | None = None


class Session:
    """One open workspace at a time per GUI process."""

    def __init__(self, app_paths: AppPaths | None = None) -> None:
        self.app_paths = app_paths or AppPaths.default()
        self._lock = threading.RLock()
        self._db: Database | None = None
        self._workspace: Workspace | None = None
        self._workspace_path: Path | None = None
        self._callbacks: list[Callable[[str, Any], None]] = []

    # ---- lifecycle ----

    def is_open(self) -> bool:
        return self._db is not None

    @property
    def db(self) -> Database:
        if self._db is None:
            raise RuntimeError("No workspace is open")
        return self._db

    @property
    def workspace(self) -> Workspace:
        if self._workspace is None:
            raise RuntimeError("No workspace is open")
        return self._workspace

    @property
    def workspace_path(self) -> Path | None:
        return self._workspace_path

    def subscribe(self, fn: Callable[[str, Any], None]) -> None:
        with self._lock:
            self._callbacks.append(fn)

    def _emit(self, event: str, payload: Any) -> None:
        for fn in list(self._callbacks):
            with contextlib.suppress(Exception):
                fn(event, payload)

    def new_workspace(self, name: str | None = None) -> Workspace:
        """Create a fresh workspace backed by an empty database."""
        self.close()
        ws = new_workspace(name or "Untitled Workspace")
        ws_dir = self.app_paths.new_workspace_dir(ws.workspace_id)
        db_path = self.app_paths.workspace_db_path(ws.workspace_id)
        self._db = Database(db_path)
        # The .trf file is metadata only. The database lives in the
        # application data tree, not beside the .trf.
        default_trf_dir = ws_dir
        ws_path = default_trf_dir / "workspace.trf"
        save_workspace(ws, ws_path)
        self._workspace = ws
        self._workspace_path = ws_path
        self._emit("workspace-opened", self.summary())
        return ws

    def open_workspace(self, path: str | os.PathLike[str]) -> Workspace:
        """Open a workspace ``.trf`` file and resolve its database.

        The database is looked up by the workspace's ``workspace_id``
        under the application data directory. The .trf file's location
        does not influence database location.
        """
        self.close()
        ws = load_workspace(path)
        db_path = self.app_paths.workspace_db_path(ws.workspace_id)
        self._db = Database(db_path)
        self._workspace = ws
        self._workspace_path = Path(path)
        self._emit("workspace-opened", self.summary())
        return ws

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                with contextlib.suppress(Exception):
                    self._db.close()
            self._db = None
            self._workspace = None
            self._workspace_path = None

    def save_workspace_as(self, path: str | os.PathLike[str]) -> None:
        """Save the .trf file to ``path``.

        The database is NOT copied. It continues to live at the
        workspace's existing application-data location. The .trf
        contains only the ``workspace_id`` that keys that database.
        """
        if self._workspace is None:
            raise RuntimeError("No workspace is open")
        self._workspace.name = Path(path).stem
        save_workspace(self._workspace, path)
        self._workspace_path = Path(path)
        self._emit("workspace-saved", str(path))

    def save_workspace(self) -> None:
        if self._workspace is None or self._workspace_path is None:
            raise RuntimeError("No workspace to save")
        save_workspace(self._workspace, self._workspace_path)

    # ---- sources ----

    def add_source(self, path: str, alias: str | None = None) -> SourceConfig:
        if self._workspace is None:
            raise RuntimeError("No workspace is open")
        abs_path = str(Path(path).resolve())
        for s in self._workspace.sources:
            if s.path == abs_path:
                return s
        cfg = SourceConfig(path=abs_path, alias=alias or Path(abs_path).name)
        self._workspace.sources.append(cfg)
        self._emit("source-added", cfg)
        return cfg

    def remove_source(self, path: str) -> None:
        if self._workspace is None:
            return
        self._workspace.sources = [s for s in self._workspace.sources if s.path != path]
        # Also delete events for that source.
        rel = self.db.execute("SELECT id FROM sources WHERE path = ?", [path])
        row = rel.fetchone()
        if row is not None:
            sid = int(row[0])
            self.db.execute("DELETE FROM events WHERE source_id = ?", [sid])
            self.db.execute("DELETE FROM sources WHERE id = ?", [sid])
        self._emit("source-removed", path)

    def list_sources(self) -> list[SourceConfig]:
        if self._workspace is None:
            return []
        return list(self._workspace.sources)

    # ---- ingestion ----

    def ingest_source(
        self,
        path: str,
        *,
        cancel: CancellationToken | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> SourceIngestionStatus:
        cfg = self.add_source(path)
        repo = EventRepository(self.db)
        source_id = repo.next_source_id()

        def _on_progress(p: IngestionProgress) -> None:
            if on_progress is not None:
                on_progress(p)
            self._emit("ingest-progress", p)

        result = ingest_file(
            self.db,
            cfg,
            source_id=source_id,
            cancel=cancel,
            on_progress=_on_progress,
        )
        self.save_workspace()
        self._emit("source-ingested", result)
        return self._status_for_source(cfg.path)

    def _status_for_source(self, path: str) -> SourceIngestionStatus:
        row = EventRepository(self.db).get_source(path)
        if row is None:
            return SourceIngestionStatus(
                path=path,
                alias=path,
                parser="?",
                total_events=0,
                bytes_read=0,
                bytes_total=0,
                parsed_lines=0,
                unstructured_lines=0,
                rejected_lines=0,
                first_event_at=None,
                last_event_at=None,
            )
        # v2 schema has 18 columns:
        # id, path, alias, enabled, parser, size_bytes, mtime_ns,
        # sample_hash, content_kind, last_ingested_at, first_event_at,
        # last_event_at, last_byte_offset, total_events, parsed_events,
        # inserted_events, parse_errors, unstructured_lines, rejected_lines
        # (19 columns total). We consume them positionally.
        (
            sid,
            path_,
            alias,
            enabled,
            parser,
            size,
            mtime_ns,
            sample_hash,
            kind,
            last_ing,
            first_event_at,
            last_event_at,
            last_off,
            total,
            parsed_events,
            inserted_events,
            parse_errors,
            unstructured,
            rejected,
        ) = row
        return SourceIngestionStatus(
            path=path_,
            alias=alias,
            parser=parser,
            total_events=int(total or 0),
            bytes_read=int(last_off or 0),
            bytes_total=int(size or 0),
            parsed_lines=int(parsed_events or 0),
            unstructured_lines=int(unstructured or 0),
            rejected_lines=int(rejected or 0),
            first_event_at=first_event_at,
            last_event_at=last_event_at,
        )

    def source_statuses(self) -> list[SourceIngestionStatus]:
        return [self._status_for_source(s.path) for s in self.list_sources()]

    # ---- queries ----

    def run_query(self, tfql: str, *, base_limit: int = 1000) -> QueryResult:
        try:
            res = tfql_execute(self.db, tfql, base_limit=base_limit)
        except QueryError:
            raise
        self._emit("query-finished", res)
        return res

    # ---- saved queries ----

    def save_query(self, name: str, query: str) -> SavedQuery:
        if self._workspace is None:
            raise RuntimeError("No workspace is open")
        for q in self._workspace.saved_queries:
            if q.name == name:
                q.query = query
                q.last_run_at = datetime.now(tz=UTC)
                return q
        sq = SavedQuery(
            name=name,
            query=query,
            created_at=datetime.now(tz=UTC),
        )
        self._workspace.saved_queries.append(sq)
        return sq

    def delete_query(self, name: str) -> None:
        if self._workspace is None:
            return
        self._workspace.saved_queries = [q for q in self._workspace.saved_queries if q.name != name]

    # ---- rules ----

    def ensure_default_rules(self) -> None:
        if self._workspace is None:
            return
        if not self._workspace.rules:
            self._workspace.rules = default_rules()

    def set_rule_enabled(self, name: str, enabled: bool) -> None:
        for r in self._workspace.rules:
            if r.name == name:
                r.enabled = enabled
                return

    def evaluate_rules(self) -> list:
        if self._workspace is None:
            return []
        ws_id = str(self._workspace_path) if self._workspace_path else "default"
        return run_all(self.db, self._workspace.rules, workspace_id=ws_id)

    # ---- summary ----

    def summary(self) -> WorkspaceSummary:
        repo = EventRepository(self.db)
        statuses = self.source_statuses()
        return WorkspaceSummary(
            name=self._workspace.name if self._workspace else "",
            db_path=self._db.path,
            sources=statuses,
            event_count=repo.count_events(),
            severity_counts=repo.count_by_severity(),
            time_range=repo.time_range(),
            workspace_path=self._workspace_path,
        )

    def severity_color(self, severity: str) -> str:
        from traceforge.ui.style import SEVERITY_COLORS

        return SEVERITY_COLORS.get(severity, SEVERITY_COLORS["UNKNOWN"])


def make_temporary_session(name: str = "temp") -> tuple[Session, Path]:
    """Create a session in a temporary directory; for tests and demos."""
    sess = Session()
    sess.new_workspace(name=name)
    return sess, sess.workspace_path
