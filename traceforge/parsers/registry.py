"""Parser registry and format auto-detection.

The registry holds the set of built-in parsers. Detection samples a small
amount of input to choose the most appropriate parser. Callers may override
the detected parser by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from traceforge.parsers.base import Parser, ParserContext
from traceforge.parsers.csv_parser import CsvLogParser
from traceforge.parsers.json_parser import JsonArrayParser, JsonLinesParser
from traceforge.parsers.text_parser import (
    ApacheAccessParser,
    CommonTextParser,
    GenericRegexParser,
    NginxAccessParser,
)


@dataclass
class DetectionResult:
    parser: Parser
    score: float
    sampled_lines: int


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[Parser] = [
            JsonLinesParser(),
            JsonArrayParser(),
            CsvLogParser(),
            ApacheAccessParser(),
            NginxAccessParser(),
            CommonTextParser(),
            GenericRegexParser(),
        ]

    def all(self) -> list[Parser]:
        return list(self._parsers)

    def by_name(self, name: str) -> Parser | None:
        for p in self._parsers:
            if p.name == name:
                return p
        return None

    def detect(
        self,
        sample: list[str],
        override: str | None = None,
        path_hint: str | None = None,
    ) -> DetectionResult:
        if override:
            parser = self.by_name(override)
            if parser is not None:
                return DetectionResult(parser=parser, score=1.0, sampled_lines=len(sample))
        ordered = list(self._parsers)
        if path_hint:
            ext = Path(path_hint).suffix.lower()
            if ext in (".ndjson", ".jsonl"):
                ordered.sort(key=lambda p: 0 if p.name in ("jsonl", "jsonarray") else 1)
            elif ext == ".csv":
                ordered.sort(key=lambda p: 0 if p.name == "csv" else 1)
            elif ext in (".log", ".txt"):
                ordered.sort(key=lambda p: 0 if p.name in ("text", "apache", "nginx") else 1)
        # If the first non-empty sample line starts with a clear ISO-8601
        # timestamp, prefer the generic text parser (its iso8600 pattern
        # matches the same string more specifically than apache).
        iso_first_line = False
        for line in sample[:1]:
            ls = line.lstrip()
            if len(ls) >= 10 and ls[:4].isdigit() and ls[4:5] == "-":
                iso_first_line = True
                break
        best: DetectionResult | None = None
        for parser in ordered:
            if parser.name == "custom_regex":
                continue
            try:
                score = parser.detect(sample)
            except Exception:
                score = 0.0
            if iso_first_line and parser.name == "text":
                # Boost text above apache/nginx when the first line is
                # clearly an ISO-8601 timestamp + level.
                score = max(score, 1.01)
            if best is None or score > best.score:
                best = DetectionResult(parser=parser, score=score, sampled_lines=len(sample))
        if best is None or best.score <= 0.0:
            text = next((p for p in self._parsers if p.name == "text"), self._parsers[0])
            return DetectionResult(parser=text, score=0.0, sampled_lines=len(sample))
        return best


DEFAULT_REGISTRY = ParserRegistry()


def build_context(
    source_path: str,
    source_alias: str,
    sample_hash: str = "",
    custom_regex: str | None = None,
    custom_field_map: dict[str, str] | None = None,
) -> ParserContext:
    return ParserContext(
        source_path=source_path,
        source_alias=source_alias,
        fingerprint_sample=sample_hash,
        custom_regex=custom_regex,
        custom_field_map=custom_field_map,
    )


def read_sample_lines(path: str, max_bytes: int = 64 * 1024) -> list[str]:
    """Return up to ``max_bytes`` worth of decoded lines for detection.

    Never reads the entire file.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return []
    text: str
    try:
        with open(p, "rb") as f:
            blob = f.read(max_bytes)
    except OSError:
        return []
    text = blob.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[:200]
