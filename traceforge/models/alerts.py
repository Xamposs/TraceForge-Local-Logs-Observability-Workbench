"""Alert model produced by the deterministic rules engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    rule_id: str
    rule_name: str
    severity: str  # INFO / NOTICE / WARNING
    fired_at: datetime
    title: str
    explanation: str
    threshold: str | None = None
    observed: str | None = None
    time_window: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    acknowledged: bool = False
    dismissed: bool = False
    sample_event_ids: list[str] = Field(default_factory=list)
