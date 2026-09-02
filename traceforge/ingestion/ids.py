"""Deterministic event ID generation.

Event identity must be reproducible for a given source ingestion so that
re-runs (and live-tail re-reads) do not create duplicates.

Identity components, in order of preference:

* source path (normalized)
* exact byte offset of the first byte of the source line in the file
  (preferred; eliminates any dependency on line-number heuristics)
* short content hash of the raw line bytes

If the exact byte offset is not available (e.g. for a CSV / JSON-array
record whose true byte position is not recoverable from a whole-document
parse), the line number is used instead. Once a parser supplies an exact
``byte_offset`` we use that — the line number is never consulted in
addition to the byte offset.

We intentionally do NOT include the file's content sample hash, because
that hash changes when the file is appended to.
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
    raw_bytes: bytes,
    *,
    byte_offset: int | None = None,
    line_number: int | None = None,
) -> str:
    """Return a stable event id.

    Prefers an exact ``byte_offset`` when available. Falls back to
    ``line_number`` only when the byte offset is unknown.

    Both values must be positive (``byte_offset=0`` is valid and refers to
    the very first byte of the file; ``line_number=1`` refers to the first
    line).
    """
    path = _normalize_path(source_path)
    content_hash = hashlib.sha1(raw_bytes).hexdigest()[:16]
    if byte_offset is not None and byte_offset >= 0:
        position_token = f"@{byte_offset}"
    elif line_number is not None and line_number > 0:
        position_token = f"L{line_number}"
    else:
        # No position available at all; fall back to a content-only
        # identity. This should be rare and only happens for whole-
        # document parsers that cannot supply any line information.
        position_token = "@?"
    payload = f"{path}|{position_token}|{content_hash}"
    digest = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"{ID_PREFIX}-{digest[:24]}"


def stamp_event_id(
    event: LogEvent,
    raw_bytes: bytes,
    sample_hash: str = "",  # kept for backwards compatibility; unused
) -> None:
    """Set ``event.event_id`` deterministically (in place)."""
    event.event_id = compute_event_id(
        event.source_path,
        raw_bytes,
        byte_offset=event.byte_offset,
        line_number=event.line_number,
    )


__all__ = ["ID_PREFIX", "compute_event_id", "stamp_event_id"]
