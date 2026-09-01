"""Workspace persistence model.

A workspace stores *references* and configuration; never raw log contents.
File format: JSON (``.trf``), schema version 1, validated by Pydantic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traceforge.models.sources import SourceConfig

WORKSPACE_FORMAT_VERSION = 1


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
    """Top-level workspace metadata persisted to disk."""

    model_config = ConfigDict(extra="forbid")

    version: int = WORKSPACE_FORMAT_VERSION
    name: str
    created_at: datetime
    updated_at: datetime
    sources: list[SourceConfig] = Field(default_factory=list)
    saved_queries: list[SavedQuery] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
    settings: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    notes: str | None = None
