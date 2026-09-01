"""Deterministic event ID generation.

Event identity must be reproducible for a given source ingestion so that
re-runs (and live-tail re-reads) do not create duplicates. We compose:

- source path (normalized)
- byte offset (preferred) or line number
- short content hash (sha1 of the raw line bytes)

We intentionally do NOT include the file's content sample hash, because
the sample hash changes when the file is appended to. Including only
line-position + content means a re-ingest of the same line (even into a
file that has grown) yields the same event_id.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from traceforge.models.events import LogEvent

ID_PREFIX = "tfevt"


def _normalize_path(p: str) -> str:
    return os.path.normcase(str(Path(p).expanduser().resolve()))


def compute_event_id(
    source_path: str,
    line_number: int,
    raw_bytes: bytes,
    byte_offset: int | None = None,
    sample_hash: str = "",
) -> str:
    path = _normalize_path(source_path)
    if byte_offset is None:
        byte_offset = 0
    content_hash = hashlib.sha1(raw_bytes).hexdigest()[:16]
    payload = f"{path}|{line_number}|{byte_offset}|{content_hash}"
    digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"{ID_PREFIX}-{digest[:24]}"


def stamp_event_id(
    event: LogEvent,
    raw_bytes: bytes,
    sample_hash: str = "",
) -> None:
    """Set ``event.event_id`` deterministically (in place)."""
    event.event_id = compute_event_id(
        event.source_path,
        event.line_number,
        raw_bytes,
        byte_offset=event.byte_offset,
        sample_hash=sample_hash,
    )
