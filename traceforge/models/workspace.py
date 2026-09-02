"""Workspace persistence model.

A workspace stores *references* and configuration; never raw log contents.
File format: JSON (``.trf``), schema version 1, validated by Pydantic.

A workspace has a stable opaque ``workspace_id`` (UUID4) that is
generated once and persisted. The id is used to locate the
workspace's database file under TraceForge's application data
directory. Moving or copying the ``.trf`` file does not change where
the derived database is resolved — the database always lives in the
application data tree keyed by ``workspace_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from traceforge.models.sources import SourceConfig

WORKSPACE_FORMAT_VERSION = 2


def _new_workspace_id() -> str:
    return f"ws-{uuid.uuid4().hex[:12]}"


class SavedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    query: str
    created_at: datetime
    last_run_at: datetime | None = None


class RuleConfig(BaseModel):
    """A configurable rule instance."""

    model_config = ConfigDict(extra="forbid")

    name: str
    rule_id: str
    enabled: bool = True
    severity: str = "NOTICE"  # INFO / NOTICE / WARNING
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = "dark"
    timezone_display: str = "local"  # local | utc
    default_row_limit: int = 1000
    live_refresh_ms: int = 200
    timestamp_format: str | None = None


class Workspace(BaseModel):
    """Top-level workspace metadata persisted to disk.

    The ``workspace_id`` is the **storage key** used to locate the
    workspace's ``events.duckdb`` inside the application data
    directory. Saving the ``.trf`` file elsewhere does not change the
    database location.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = WORKSPACE_FORMAT_VERSION
    workspace_id: str = Field(default_factory=_new_workspace_id)
    name: str
    created_at: datetime
    updated_at: datetime
    sources: list[SourceConfig] = Field(default_factory=list)
    saved_queries: list[SavedQuery] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
    settings: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    notes: str | None = None

    @field_validator("workspace_id")
    @classmethod
    def _validate_workspace_id(cls, v: str) -> str:
        if not v or not v.startswith("ws-"):
            raise ValueError("workspace_id must be a non-empty string starting with 'ws-'")
        return v
