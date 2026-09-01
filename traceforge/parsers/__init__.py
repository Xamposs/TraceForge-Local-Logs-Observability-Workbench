"""Parser subsystem.

Public surface used by the ingestion pipeline:

* :class:`ParserRegistry` for detection / lookup.
* :class:`ParserContext` and the per-parser :class:`Parser` interface.
* :class:`ParsedRecord` returned by parser streams.
"""

from __future__ import annotations

from traceforge.parsers.base import ParsedRecord, Parser, ParserContext
from traceforge.parsers.registry import (
    DEFAULT_REGISTRY,
    DetectionResult,
    ParserRegistry,
    build_context,
    read_sample_lines,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "DetectionResult",
    "ParsedRecord",
    "Parser",
    "ParserContext",
    "ParserRegistry",
    "build_context",
    "read_sample_lines",
]
