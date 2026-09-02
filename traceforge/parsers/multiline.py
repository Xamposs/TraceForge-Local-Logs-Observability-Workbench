"""Multiline event assembly.

A *primary* line is one that looks like a "start of event" line. Subsequent
*continuation* lines (no recognised timestamp/level prefix) are folded onto
the previous primary event's message.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

# Common "looks like a primary line" heuristics.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ISO 8601 / common text timestamp at the very start.
    re.compile(r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"),
    # Apache / nginx-like: 127.0.0.1 - - [date] ...
    re.compile(r"^\S+\s+\S+\s+\S+\s+\["),
    # Bracketed timestamp: [2026-09-01 14:32:18]
    re.compile(r"^\s*\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[^\]]*\]"),
    # JSON object
    re.compile(r"^\s*\{.*\}\s*$"),
    # Syslog-ish: Sep 01 14:32:18 host app: message
    re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\S+\s"),
)


def looks_like_primary(line: str) -> bool:
    if not line:
        return False
    return any(p.match(line) for p in _PATTERNS)


@dataclass
class MultilineAssembler:
    """Stateful assembler that yields primary lines with folded continuations.

    Usage::

        asm = MultilineAssembler()
        for line in lines:
            for event_text in asm.feed(line):
                ...  # parse event_text
        for event_text in asm.flush():
            ...  # trailing events
    """

    predicate: Callable[[str], bool] = looks_like_primary
    max_event_bytes: int = 200_000

    _buffer: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._buffer is None:
            self._buffer = []

    def feed(self, line: str) -> Iterator[str]:
        if self.predicate(line):
            if self._buffer:
                yield self._join()
            self._buffer = [line]
        else:
            if self._buffer:
                self._buffer.append(line)
            else:
                # Stray continuation before any primary; treat as primary.
                self._buffer = [line]
        return

    def flush(self) -> Iterator[str]:
        if self._buffer:
            yield self._join()
            self._buffer = []

    def _join(self) -> str:
        text = "\n".join(self._buffer)
        if len(text) > self.max_event_bytes:
            text = text[: self.max_event_bytes] + "\n...[truncated]"
        return text
