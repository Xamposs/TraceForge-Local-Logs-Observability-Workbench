"""Text log parsers.

Three families are supported:

* ``CommonTextParser`` — a deterministic regex pattern set covering the
  most common textual log formats. The parser keeps a
  :class:`MultilineAssembler` across the entire line stream so
  multi-line stack traces are correctly folded onto their primary
  line.
* ``ApacheAccessParser`` / ``NginxAccessParser`` — combined-log format
  (these are very similar; we share a single regex).
* ``GenericRegexParser`` — a user-supplied regex with a field-name map.

When no pattern matches, a line is ingested as ``severity=UNKNOWN``
with the raw line as message; the pipeline never silently drops lines.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from traceforge.ingestion.ids import stamp_event_id
from traceforge.models.events import LogEvent, normalize_severity
from traceforge.parsers.base import ParsedRecord, Parser, ParserContext, SourceLine
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
            r"(?P<message>.*)$",
            re.DOTALL,
        ),
    ),
    (
        "bracketed",
        re.compile(
            r"^\s*\[(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]"
            r"\s*\[(?P<level>[A-Z]+)\]\s*"
            r"(?P<message>.*)$",
            re.DOTALL,
        ),
    ),
    (
        "syslog",
        re.compile(
            r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
            r"(?P<host>\S+)\s+(?P<service>[^:]+):\s*(?P<message>.*)$",
            re.DOTALL,
        ),
    ),
    (
        "apache",
        re.compile(
            r"^(?P<host>\S+)\s+\S+\s+\S+\s+\[(?P<timestamp>[^\]]+)\]\s+"
            r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/[\d.]+"\s+'
            r"(?P<status>\d{3})\s+(?P<size>\S+)\s*"
            r'(?:"(?P<referer>[^"]*)"\s*)?'
            r'(?:"(?P<user_agent>[^"]*)"\s*)?',
            re.DOTALL,
        ),
    ),
    (
        "py-log",
        re.compile(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+"
            r"(?P<level>[A-Z]+)\s+"
            r"(?P<logger>\S+)\s+-\s+"
            r"(?P<message>.*)$",
            re.DOTALL,
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
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _truncate_text(value: str, limit: int = 8000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class CommonTextParser(Parser):
    """Text parser with cross-line multi-line stack-trace assembly.

    The parser keeps a single :class:`MultilineAssembler` for the
    duration of one ``parse()`` call. The pipeline must therefore feed
    the entire logical source as a single iterable to ``parse()`` —
    calling ``parse()`` once per line is unsupported and will produce
    incorrect output.
    """

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
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        now = datetime.now(tz=UTC)
        asm = MultilineAssembler()
        # The SourceLine that opened the currently-buffered event.
        # This is the *primary* line — the line the event should be
        # attributed to.
        primary_src: SourceLine | None = None
        for src in lines:
            was_empty_before = not asm._buffer
            events = list(asm.feed(src.text))
            for event_text in events:
                # The event was flushed because ``src`` is a new
                # primary. The event itself was opened by the previous
                # primary, so its source position is the previous
                # primary_src (or ``src`` for the very first event).
                src_for_event = primary_src if primary_src is not None else src
                ev, unstructured = self._build(event_text, now, src_for_event)
                yield ParsedRecord(event=ev, unstructured=unstructured)
            # After the feed, decide what ``primary_src`` should be.
            # If we just emitted an event above (events was non-empty),
            # the new buffer (if any) was opened by ``src``, which is a
            # new primary. Otherwise the buffer state is unchanged.
            if events or was_empty_before and asm._buffer:
                primary_src = src
        for event_text in asm.flush():
            ev, unstructured = self._build(
                event_text,
                now,
                primary_src if primary_src is not None else _synthetic_line(context.start_line),
            )
            yield ParsedRecord(event=ev, unstructured=unstructured)
            if was_empty_before and asm._buffer:
                primary_src = src
        for event_text in asm.flush():
            ev, unstructured = self._build(
                event_text,
                now,
                primary_src if primary_src is not None else _synthetic_line(context.start_line),
            )
            yield ParsedRecord(event=ev, unstructured=unstructured)

    def _build(
        self,
        text: str,
        now: datetime,
        src: SourceLine,
    ) -> tuple[LogEvent, bool]:
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
                source="",
                source_path="",
                line_number=src.line_number,
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
            ev.byte_offset = src.byte_offset
            ev.line_number = src.line_number
            stamp_event_id(ev, text.encode("utf-8", errors="replace"), "")
            return ev, False
        truncated = text.endswith("...[truncated]")
        msg = text[:8000]
        if truncated and "[truncated]" not in msg:
            msg = msg.rstrip() + "...[truncated]"
        ev = LogEvent(
            event_id="",
            source="",
            source_path="",
            line_number=src.line_number,
            timestamp=None,
            ingested_at=now,
            severity="UNKNOWN",
            message=msg,
            raw_text=text,
            raw_format="text",
        )
        ev.byte_offset = src.byte_offset
        ev.line_number = src.line_number
        stamp_event_id(ev, text.encode("utf-8", errors="replace"), "")
        return ev, True

    def is_multiline_capable(self) -> bool:
        return True


def _synthetic_line(line_no: int) -> SourceLine:
    return SourceLine(byte_offset=0, line_number=line_no, text="", raw_bytes=b"")


class ApacheAccessParser(CommonTextParser):
    name = "apache"

    def is_multiline_capable(self) -> bool:
        return False


class NginxAccessParser(CommonTextParser):
    name = "nginx"

    def is_multiline_capable(self) -> bool:
        return False


class GenericRegexParser(Parser):
    """User-supplied regex parser."""

    name = "custom_regex"

    def detect(self, sample_lines: list[str]) -> float:
        return 0.0

    def parse(
        self,
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        if not context.custom_regex:
            return
        try:
            pat = re.compile(context.custom_regex)
        except re.error:
            now = datetime.now(tz=UTC)
            for src in lines:
                ev = LogEvent(
                    event_id="",
                    source=context.source_alias,
                    source_path=context.source_path,
                    line_number=src.line_number,
                    timestamp=None,
                    ingested_at=now,
                    severity="UNKNOWN",
                    message=src.text[:8000],
                    raw_text=src.text,
                    raw_format="regex",
                )
                ev.byte_offset = src.byte_offset
                ev.line_number = src.line_number
                stamp_event_id(ev, src.raw_bytes, context.fingerprint_sample)
                yield ParsedRecord(event=ev, unstructured=True)
            return
        field_map = context.custom_field_map or {}
        now = datetime.now(tz=UTC)
        for src in lines:
            text = src.text
            m = pat.search(text)
            if not m:
                ev = LogEvent(
                    event_id="",
                    source=context.source_alias,
                    source_path=context.source_path,
                    line_number=src.line_number,
                    timestamp=None,
                    ingested_at=now,
                    severity="UNKNOWN",
                    message=text[:8000],
                    raw_text=text,
                    raw_format="regex",
                )
                ev.byte_offset = src.byte_offset
                ev.line_number = src.line_number
                stamp_event_id(ev, src.raw_bytes, context.fingerprint_sample)
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
                line_number=src.line_number,
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
            ev.byte_offset = src.byte_offset
            ev.line_number = src.line_number
            stamp_event_id(ev, src.raw_bytes, context.fingerprint_sample)
            yield ParsedRecord(event=ev, unstructured=False)

    def is_multiline_capable(self) -> bool:
        return True
