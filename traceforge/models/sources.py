"""Source configuration / parser override models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from traceforge.models.events import SourceFingerprint


class SourceConfig(BaseModel):
    """User-facing configuration for a single log source."""

    model_config = ConfigDict(extra="forbid")

    path: str
    alias: str | None = None
    enabled: bool = True
    parser_override: str | None = None
    custom_regex: str | None = None
    custom_field_map: dict[str, str] = Field(default_factory=dict)
    last_fingerprint: SourceFingerprint | None = None
    last_ingested_at: datetime | None = None
