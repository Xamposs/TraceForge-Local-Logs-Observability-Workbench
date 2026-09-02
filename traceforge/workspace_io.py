"""Workspace persistence (``.trf`` JSON file).

A workspace file stores *references* and configuration only. The
derived database lives under TraceForge's application data directory
keyed by the workspace's stable ``workspace_id`` (UUID4). Saving the
``.trf`` to a different location does not move the database.

Format versions:
* v1 — no ``workspace_id``; database is expected beside the ``.trf``.
  On load we synthesise a stable id from the file's parent directory
  so the existing on-disk database remains findable.
* v2 — adds an explicit ``workspace_id`` field. This is the new
  default.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from traceforge.models.sources import SourceConfig
from traceforge.models.workspace import (
    WORKSPACE_FORMAT_VERSION,
    RuleConfig,
    SavedQuery,
    Workspace,
    WorkspaceSettings,
)


def _stable_id_from_path(path: Path) -> str:
    """Generate a deterministic workspace_id from a v1 file's path.

    v1 workspaces had no id, so we derive one from the absolute
    file path. The same path always produces the same id, so a
    re-load of a v1 ``.trf`` keeps pointing at the same database.
    """
    h = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"ws-v1-{h}"


def new_workspace(name: str) -> Workspace:
    now = datetime.now(tz=UTC)
    return Workspace(
        version=WORKSPACE_FORMAT_VERSION,
        name=name,
        created_at=now,
        updated_at=now,
        sources=[],
        saved_queries=[],
        rules=[],
        settings=WorkspaceSettings(),
    )


def load_workspace(path: str | os.PathLike[str]) -> Workspace:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid workspace file: not a JSON object")
    # v1 -> v2 migration: synthesize a stable workspace_id.
    if "workspace_id" not in raw:
        raw["workspace_id"] = _stable_id_from_path(p)
        raw["version"] = WORKSPACE_FORMAT_VERSION
    try:
        ws = Workspace.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid workspace file: {e}") from e
    return ws


def save_workspace(workspace: Workspace, path: str | os.PathLike[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    workspace.updated_at = datetime.now(tz=UTC)
    payload = workspace.model_dump(mode="json")
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "RuleConfig",
    "SavedQuery",
    "SourceConfig",
    "Workspace",
    "WorkspaceSettings",
    "load_workspace",
    "new_workspace",
    "save_workspace",
]
