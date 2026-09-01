"""TraceForge local application-data and workspace paths.

All derived indexes, DuckDB databases, workspaces, caches, and exports live
under the OS-appropriate user data directory:

- Windows: ``%LOCALAPPDATA%\\TraceForge``
- Linux:   ``$XDG_DATA_HOME/traceforge`` (default ``~/.local/share/traceforge``)
- macOS:   ``~/Library/Application Support/TraceForge``

Source log files are NEVER stored or modified here. Only references to them
and derived metadata.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

APP_NAME: Final = "TraceForge"


def _local_appdata_root() -> Path:
    """Return the OS-appropriate per-user data directory for TraceForge."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "traceforge"
    return Path.home() / ".local" / "share" / "traceforge"


def _local_cache_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME / "cache"
        return Path.home() / "AppData" / "Local" / APP_NAME / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "traceforge"
    return Path.home() / ".cache" / "traceforge"


def _local_config_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "traceforge"
    return Path.home() / ".config" / "traceforge"


@dataclass(frozen=True)
class AppPaths:
    """Resolved application paths.

    All directories are guaranteed to exist on instantiation.
    """

    data_dir: Path
    workspaces_dir: Path
    cache_dir: Path
    config_dir: Path
    temp_dir: Path

    @classmethod
    def default(cls) -> AppPaths:
        data = _local_appdata_root()
        cache = _local_cache_root()
        config = _local_config_root()
        for root in (data, cache, config):
            root.mkdir(parents=True, exist_ok=True)
        workspaces = data / "workspaces"
        workspaces.mkdir(parents=True, exist_ok=True)
        temp_root = data / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data,
            workspaces_dir=workspaces,
            cache_dir=cache,
            config_dir=config,
            temp_dir=temp_root,
        )

    def new_workspace_dir(self, slug: str | None = None) -> Path:
        """Create and return a fresh per-workspace directory."""
        import time
        import uuid

        name = slug or time.strftime("%Y%m%d-%H%M%S")
        candidate = self.workspaces_dir / f"ws-{name}-{uuid.uuid4().hex[:8]}"
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate

    def new_temp_path(self, prefix: str = "tf-", suffix: str = "") -> Path:
        name = f"{prefix}{os.getpid()}-{uuid_suffix()}{suffix}"
        return self.temp_dir / name

    def session_export_dir(self) -> Path:
        p = self.data_dir / "exports"
        p.mkdir(parents=True, exist_ok=True)
        return p


def uuid_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def safe_join(base: Path, *parts: str) -> Path:
    """Join paths and ensure the result is within ``base`` (defence-in-depth)."""
    p = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    if base_resolved not in p.parents and p != base_resolved:
        raise ValueError(f"Path escapes base directory: {p}")
    return p


def normalize_source_path(raw: str | os.PathLike[str]) -> Path:
    """Return an absolute, resolved Path for a source log file reference."""
    p = Path(os.fspath(raw)).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    else:
        p = p.resolve()
    return p
