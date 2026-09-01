"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from traceforge.app_paths import AppPaths
from traceforge.storage import Database


@pytest.fixture()
def temp_dir() -> Path:
    with tempfile.TemporaryDirectory(prefix="traceforge-tests-") as d:
        yield Path(d)


@pytest.fixture()
def database(temp_dir: Path) -> Database:
    db = Database(temp_dir / "test.duckdb")
    yield db
    db.close()


@pytest.fixture()
def isolated_app_paths(temp_dir: Path, monkeypatch) -> AppPaths:
    """Force all app paths to live under a per-test temporary directory."""
    paths = AppPaths(
        data_dir=temp_dir / "data",
        workspaces_dir=temp_dir / "data" / "workspaces",
        cache_dir=temp_dir / "data" / "cache",
        config_dir=temp_dir / "data" / "config",
        temp_dir=temp_dir / "data" / "tmp",
    )
    for p in (paths.data_dir, paths.workspaces_dir, paths.cache_dir, paths.config_dir, paths.temp_dir):
        p.mkdir(parents=True, exist_ok=True)
    return paths
