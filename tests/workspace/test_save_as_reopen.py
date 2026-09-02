"""Workspace Save As / reopen regression.

Workspace storage uses a stable opaque ``workspace_id`` that lives in
TraceForge's application data directory. The ``.trf`` file is metadata
only. Moving the .trf file to another directory does not move the
database; reopening the moved .trf file resolves to the same database
contents.
"""

from __future__ import annotations

import json

import pytest

from traceforge.app_paths import AppPaths
from traceforge.models.sources import SourceConfig
from traceforge.storage import EventRepository
from traceforge.ui.session import Session
from traceforge.workspace_io import load_workspace


def test_save_as_keeps_database_at_original_location(tmp_path) -> None:
    paths = AppPaths(
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "data" / "workspaces",
        cache_dir=tmp_path / "data" / "cache",
        config_dir=tmp_path / "data" / "config",
        temp_dir=tmp_path / "data" / "tmp",
    )
    for p in (
        paths.data_dir,
        paths.workspaces_dir,
        paths.cache_dir,
        paths.config_dir,
        paths.temp_dir,
    ):
        p.mkdir(parents=True, exist_ok=True)

    session = Session(paths)
    ws = session.new_workspace("My Demo")
    workspace_id = ws.workspace_id
    # Ingest some events.
    log = tmp_path / "demo.log"
    log.write_text(
        "2026-09-01 12:00:00 INFO hello\n"
        "2026-09-01 12:00:01 INFO world\n"
        "2026-09-01 12:00:02 INFO again\n",
        encoding="utf-8",
    )
    cfg = SourceConfig(path=str(log), alias="demo")
    session.ingest_source(str(log))
    count_before = EventRepository(session.db).count_events()
    assert count_before == 3

    original_trf = session.workspace_path
    assert original_trf is not None
    original_db = session.db.path
    assert original_db.exists()

    # Save As to a totally different directory.
    new_trf_dir = tmp_path / "saved_elsewhere"
    new_trf_dir.mkdir()
    new_trf = new_trf_dir / "demo.trf"
    session.save_workspace_as(new_trf)
    assert session.workspace_path == new_trf
    # Database must NOT be in new_trf_dir.
    assert not (new_trf_dir / "events.duckdb").exists()
    # Database must still be at the original app-data location.
    assert original_db.exists()
    # Re-ingesting the same source into the new trf should reuse the
    # same workspace_id (so the existing database is reused).
    reloaded = load_workspace(new_trf)
    assert reloaded.workspace_id == workspace_id
    session.close()

    # Open the moved .trf and confirm we land on the same database.
    session2 = Session(paths)
    ws2 = session2.open_workspace(new_trf)
    assert ws2.workspace_id == workspace_id
    count_after = EventRepository(session2.db).count_events()
    # Count must be preserved (the database was reused, not recreated).
    assert count_after == count_before
    session2.close()


def test_open_nonexistent_workspace_raises(tmp_path) -> None:
    paths = AppPaths(
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "data" / "workspaces",
        cache_dir=tmp_path / "data" / "cache",
        config_dir=tmp_path / "data" / "config",
        temp_dir=tmp_path / "data" / "tmp",
    )
    for p in (
        paths.data_dir,
        paths.workspaces_dir,
        paths.cache_dir,
        paths.config_dir,
        paths.temp_dir,
    ):
        p.mkdir(parents=True, exist_ok=True)
    session = Session(paths)
    with pytest.raises(FileNotFoundError):
        session.open_workspace(tmp_path / "nope.trf")


def test_v1_workspace_file_loads_with_synthesized_id(tmp_path) -> None:
    """A v1 workspace file (no ``workspace_id``) must load with a
    deterministic synthesised id, so the database location is
    stable across re-loads."""
    paths = AppPaths(
        data_dir=tmp_path / "data",
        workspaces_dir=tmp_path / "data" / "workspaces",
        cache_dir=tmp_path / "data" / "cache",
        config_dir=tmp_path / "data" / "config",
        temp_dir=tmp_path / "data" / "tmp",
    )
    for p in (
        paths.data_dir,
        paths.workspaces_dir,
        paths.cache_dir,
        paths.config_dir,
        paths.temp_dir,
    ):
        p.mkdir(parents=True, exist_ok=True)
    trf = tmp_path / "v1.trf"
    payload = {
        "version": 1,
        "name": "Old WS",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "sources": [],
        "saved_queries": [],
        "rules": [],
        "settings": {
            "theme": "dark",
            "timezone_display": "local",
            "default_row_limit": 1000,
            "live_refresh_ms": 200,
            "timestamp_format": None,
        },
        "notes": None,
    }
    trf.write_text(json.dumps(payload), encoding="utf-8")
    ws1 = load_workspace(trf)
    ws2 = load_workspace(trf)
    # Re-loading produces the same synthesised id (stable across
    # sessions).
    assert ws1.workspace_id == ws2.workspace_id
    assert ws1.workspace_id.startswith("ws-v1-")
