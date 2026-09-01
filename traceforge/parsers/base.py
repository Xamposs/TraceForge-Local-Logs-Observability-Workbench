"""Parser base interface."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from traceforge.models.events import LogEvent


@dataclass
class ParseStats:
    total_lines: int = 0
    parsed_lines: int = 0
    unstructured_lines: int = 0
    rejected_lines: int = 0


@dataclass
class ParsedRecord:
    event: LogEvent
    unstructured: bool = False  # True if message couldn't be structured


@dataclass
class ParserContext:
    """Context passed to a parser for a particular source."""

    source_path: str
    source_alias: str
    fingerprint_sample: str = ""  # hex sample hash for stable event IDs
    max_line_bytes: int = 1_000_000  # 1MB line cap
    custom_regex: str | None = None
    custom_field_map: dict[str, str] | None = None
    start_line: int = 1


class Parser:
    """Abstract parser.

    Implementations should be cheap to construct (no I/O in __init__).
    """

    name: str = "base"

    def detect(self, sample_lines: list[str]) -> float:
        """Return a confidence score in [0.0, 1.0] given a sample of input."""
        return 0.0

    def parse(
        self,
        lines: Iterable[str],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        raise NotImplementedError

    def is_multiline_capable(self) -> bool:
        return False
