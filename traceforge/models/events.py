"""Pydantic models for the normalized event representation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Well-known severity buckets. Unknown text is preserved separately.
SEVERITIES: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARN",
    "WARNING",
    "ERROR",
    "FATAL",
    "CRITICAL",
    "UNKNOWN",
)


class LogEvent(BaseModel):
    """A canonical, normalized log event.

    Fields are intentionally permissive: parsers are allowed to leave anything
    they cannot determine as ``None``. We do not fabricate metadata.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    # Identity — deterministic, never Date.now()-only.
    event_id: str
    source: str
    source_path: str
    line_number: int = Field(ge=1)
    byte_offset: int | None = Field(default=None, ge=0)
    raw_format: str = "unknown"

    # Time.
    timestamp: datetime | None = None
    ingested_at: datetime

    # Core.
    severity: str = "UNKNOWN"
    message: str = ""
    raw_text: str = ""

    # Identifiers.
    service: str | None = None
    logger: str | None = None
    host: str | None = None
    process: str | None = None
    thread: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None

    # Optional metrics.
    duration_ms: float | None = None
    status_code: int | None = None
    exception_type: str | None = None

    # Extra structured metadata not in the canonical fields.
    attributes: dict[str, Any] = Field(default_factory=dict)


class SourceStats(BaseModel):
    """Ingestion-quality statistics for a single source."""

    model_config = ConfigDict(extra="forbid")

    path: str
    parser: str
    total_lines: int = 0
    parsed_lines: int = 0
    unstructured_lines: int = 0
    rejected_lines: int = 0
    bytes_read: int = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    parse_errors: list[str] = Field(default_factory=list)


class SourceFingerprint(BaseModel):
    """Identity info for a log source to detect rotation / change."""

    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    mtime_ns: int
    sample_hash: str
    content_kind: str = "text"

    def fingerprint_key(self) -> str:
        return f"{self.path}|{self.size}|{self.mtime_ns}|{self.sample_hash}"


def is_known_severity(value: str) -> bool:
    return value.upper() in SEVERITIES


def normalize_severity(value: str | None) -> str:
    """Map various severity spellings onto a fixed set."""
    if not value:
        return "UNKNOWN"
    s = value.strip().upper()
    if not s:
        return "UNKNOWN"
    if s in SEVERITIES:
        return s
    # Common aliases.
    aliases = {
        "WARNING": "WARN",
        "ERR": "ERROR",
        "FATAL": "FATAL",
        "CRIT": "CRITICAL",
        "EMERG": "FATAL",
        "EMERGENCY": "FATAL",
        "ALERT": "FATAL",
        "PANIC": "FATAL",
        "TRACE": "TRACE",
        "DBG": "DEBUG",
        "INFORMATION": "INFO",
        "NOTICE": "NOTICE",
    }
    return aliases.get(s, s)
