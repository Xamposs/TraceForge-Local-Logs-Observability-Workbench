"""Text log parsers.

Three families are supported:

* ``GenericTextParser`` — a deterministic regex pattern set covering the
  most common textual log formats. If no pattern matches, the line is
  ingested as ``severity=UNKNOWN`` with the raw line as message.
* ``ApacheAccessParser`` / ``NginxAccessParser`` — combined-log format
  (these are very similar; we share a single regex).
* ``GenericRegexParser`` — a user-supplied regex with a field-name map.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from traceforge.ingestion.ids import stamp_event_id
from traceforge.models.events import LogEvent, normalize_severity
from traceforge.parsers.base import ParsedRecord, Parser, ParserContext
from traceforge.parsers.multiline import MultilineAssembler

_TRY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "iso8600",
        re.compile(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
            r"(?:Z|[+-]\d{2}:?\d{2})?)\s+"
            r"(?P<level>TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|FATAL|CRITICAL)"
            r"\s*"
            r"(?:\[(?P<service>[^\]]+)\]\s*)?"
            r"(?P<message>.*)$"
        ),
    ),
    (
        "bracketed",
        re.compile(
            r"^\s*\[(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]"
            r"\s*\[(?P<level>[A-Z]+)\]\s*"
            r"(?P<message>.*)$"
        ),
    ),
    (
        "syslog",
        re.compile(
            r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
            r"(?P<host>\S+)\s+(?P<service>[^:]+):\s*(?P<message>.*)$"
        ),
    ),
    (
        "apache",
        re.compile(
            r"^(?P<host>\S+)\s+\S+\s+\S+\s+\[(?P<timestamp>[^\]]+)\]\s+"
            r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/[\d.]+"\s+'
            r"(?P<status>\d{3})\s+(?P<size>\S+)\s*"
            r'(?:"(?P<referer>[^"]*)"\s*)?'
            r'(?:"(?P<user_agent>[^"]*)"\s*)?'
        ),
    ),
    (
        "py-log",
        re.compile(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+"
            r"(?P<level>[A-Z]+)\s+"
            r"(?P<logger>\S+)\s+-\s+"
            r"(?P<message>.*)$"
        ),
    ),
)


_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S %z",
    "%b %d %H:%M:%S",
)


def _parse_text_timestamp(value: str) -> datetime | None:
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class CommonTextParser(Parser):
    name = "text"

    def detect(self, sample_lines: list[str]) -> float:
        if not sample_lines:
            return 0.0
        good = 0
        for line in sample_lines[:50]:
            if any(p.match(line) for _, p in _TRY_PATTERNS):
                good += 1
        return min(1.0, good / max(1, len(sample_lines)))

    def parse(
        self,
        lines,
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        asm = MultilineAssembler()
        start = max(1, context.start_line)
        line_index = start
        for raw in lines:
            for event_text in asm.feed(raw.rstrip("\n\r")):
                yield self._build(event_text, context, line_index)
                line_index += 1
        for event_text in asm.flush():
            yield self._build(event_text, context, line_index)
            line_index += 1

    def _build(self, text: str, context: ParserContext, line_no: int) -> ParsedRecord:
        now = datetime.now(tz=UTC)
        for _name, pat in _TRY_PATTERNS:
            m = pat.match(text)
            if not m:
                continue
            g = m.groupdict()
            ts = _parse_text_timestamp(g.get("timestamp", "") or "")
            level = normalize_severity(g.get("level"))
            message = g.get("message") or text
            service = g.get("service")
            ev = LogEvent(
                event_id="",
                source=context.source_alias,
                source_path=context.source_path,
                line_number=line_no,
                timestamp=ts,
                ingested_at=now,
                severity=level,
                message=message.strip()[:8000],
                raw_text=text,
                raw_format="text",
                service=service.strip() if service else None,
                host=g.get("host"),
                logger=g.get("logger"),
                status_code=_safe_int(g.get("status")),
            )
            # Use position of the matched start in source as a stable line reference.
            # When we cannot determine it, line_number stays 0 and byte_offset encodes
            # the raw text hash for dedup.
            stamp_event_id(ev, text.encode("utf-8", errors="replace"), context.fingerprint_sample)
            return ParsedRecord(event=ev, unstructured=False)
        # Fallback: UNKNOWN text.
        truncated = text.endswith("...[truncated]")
        msg = text[:8000]
        if truncated and "[truncated]" not in msg:
            msg = msg.rstrip() + "...[truncated]"
        ev = LogEvent(
            event_id="",
            source=context.source_alias,
            source_path=context.source_path,
            line_number=line_no,
            timestamp=None,
            ingested_at=now,
            severity="UNKNOWN",
            message=msg,
            raw_text=text,
            raw_format="text",
        )
        stamp_event_id(ev, text.encode("utf-8", errors="replace"), context.fingerprint_sample)
        return ParsedRecord(event=ev, unstructured=True)


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class ApacheAccessParser(CommonTextParser):
    name = "apache"


class NginxAccessParser(CommonTextParser):
    name = "nginx"


class GenericRegexParser(Parser):
    """User-supplied regex parser.

    ``context.custom_regex`` must be a regex with named groups. The
    ``context.custom_field_map`` maps known LogEvent field names to the
    named group, e.g. ``{"timestamp": "ts", "level": "lvl", ...}``.
    """

    name = "custom_regex"
    _pattern: re.Pattern[str] | None = None

    def detect(self, sample_lines: list[str]) -> float:
        # Detection is only meaningful if a regex is supplied up front.
        return 0.0

    def parse(self, lines, context: ParserContext) -> Iterator[ParsedRecord | None]:
        if not context.custom_regex:
            return
        try:
            pat = re.compile(context.custom_regex)
        except re.error:
            # Bad regex: surface the entire line as UNKNOWN instead of
            # silently dropping content.
            now = datetime.now(tz=UTC)
            for line_no, raw in enumerate(lines, start=1):
                text = raw.rstrip("\n\r")
                ev = LogEvent(
                    event_id="",
                    source=context.source_alias,
                    source_path=context.source_path,
                    line_number=line_no,
                    timestamp=None,
                    ingested_at=now,
                    severity="UNKNOWN",
                    message=text[:8000],
                    raw_text=text,
                    raw_format="regex",
                )
                stamp_event_id(ev, text.encode("utf-8", errors="replace"), context.fingerprint_sample)
                yield ParsedRecord(event=ev, unstructured=True)
            return
        field_map = context.custom_field_map or {}
        now = datetime.now(tz=UTC)
        for line_no, raw in enumerate(lines, start=1):
            text = raw.rstrip("\n\r")
            m = pat.search(text)
            if not m:
                ev = LogEvent(
                    event_id="",
                    source=context.source_alias,
                    source_path=context.source_path,
                    line_number=line_no,
                    timestamp=None,
                    ingested_at=now,
                    severity="UNKNOWN",
                    message=text[:8000],
                    raw_text=text,
                    raw_format="regex",
                )
                stamp_event_id(ev, text.encode("utf-8", errors="replace"), context.fingerprint_sample)
                yield ParsedRecord(event=ev, unstructured=True)
                continue
            groups = m.groupdict()
            ts = (
                _parse_text_timestamp(groups.get(field_map.get("timestamp", ""), "") or "")
                if "timestamp" in field_map
                else None
            )
            lvl = (
                normalize_severity(groups.get(field_map.get("level", "")))
                if "level" in field_map
                else "UNKNOWN"
            )
            msg = groups.get(field_map.get("message", ""), text)
            svc = groups.get(field_map.get("service", "")) if "service" in field_map else None
            req = groups.get(field_map.get("request_id", "")) if "request_id" in field_map else None
            trace = groups.get(field_map.get("trace_id", "")) if "trace_id" in field_map else None
            dur = (
                _safe_int(groups.get(field_map.get("duration_ms", "")))
                if "duration_ms" in field_map
                else None
            )
            status = (
                _safe_int(groups.get(field_map.get("status_code", "")))
                if "status_code" in field_map
                else None
            )
            ev = LogEvent(
                event_id="",
                source=context.source_alias,
                source_path=context.source_path,
                line_number=line_no,
                timestamp=ts,
                ingested_at=now,
                severity=lvl,
                message=str(msg)[:8000],
                raw_text=text,
                raw_format="regex",
                service=svc,
                request_id=req,
                trace_id=trace,
                duration_ms=float(dur) if dur is not None else None,
                status_code=status,
            )
            stamp_event_id(ev, text.encode("utf-8", errors="replace"), context.fingerprint_sample)
            yield ParsedRecord(event=ev, unstructured=False)

    def is_multiline_capable(self) -> bool:
        return True
