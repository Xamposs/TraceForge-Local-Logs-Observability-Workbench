"""Parser base interface.

A parser consumes a stream of source lines and yields zero or more
:class:`ParsedRecord` objects. The parser is responsible for any
multi-line state across the line stream — the pipeline does not
reset the parser between lines.

Parsers that only need a single line at a time may simply iterate the
incoming stream and yield events; parsers that need the whole document
(CSV, JSON array) buffer internally.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceLine:
    """A single source line as observed by the ingestion pipeline.

    ``byte_offset`` is the absolute byte position of the first byte of
    this line in the original file. ``line_number`` starts at 1 for the
    first line emitted by the reader (regardless of ``start_offset``).
    ``raw_bytes`` is the original byte sequence read from disk (with
    trailing CR/LF stripped, as the reader handles line termination).
    """

    byte_offset: int
    line_number: int
    text: str
    raw_bytes: bytes


@dataclass
class ParseStats:
    total_lines: int = 0
    parsed_lines: int = 0
    unstructured_lines: int = 0
    rejected_lines: int = 0


@dataclass
class ParsedRecord:
    """A single record produced by a parser.

    The pipeline fills in ``byte_offset`` and ``line_number`` from the
    first :class:`SourceLine` consumed for this event. Parsers should
    leave those fields as zero so the pipeline knows to fill them.
    """

    event: object  # traceforge.models.events.LogEvent
    unstructured: bool = False  # True if message could not be structured


@dataclass
class ParserContext:
    """Context passed to a parser for a particular source."""

    source_path: str
    source_alias: str
    fingerprint_sample: str = ""
    max_line_bytes: int = 1_000_000
    custom_regex: str | None = None
    custom_field_map: dict[str, str] | None = None
    start_line: int = 1


@runtime_checkable
class Parser(Protocol):
    """A streaming line parser."""

    name: str

    def detect(self, sample_lines: list[str]) -> float:
        """Return a confidence score in [0.0, 1.0] given a sample of input."""
        ...

    def parse(
        self,
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        """Yield events from a continuous line stream.

        Implementations may hold internal state across calls; the pipeline
        does not reset state between ``SourceLine``s.
        """
        ...

    def is_multiline_capable(self) -> bool:
        """Whether the parser holds state across multiple lines."""
        ...


__all__ = [
    "ParseStats",
    "ParsedRecord",
    "Parser",
    "ParserContext",
    "SourceLine",
]
