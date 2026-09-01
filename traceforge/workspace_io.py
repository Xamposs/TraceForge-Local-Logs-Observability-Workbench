"""Workspace persistence (``.trf`` JSON file)."""

from __future__ import annotations

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
