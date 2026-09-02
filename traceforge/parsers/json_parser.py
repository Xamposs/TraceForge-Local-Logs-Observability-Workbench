"""JSON Lines / NDJSON parser."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

from traceforge.ingestion.ids import stamp_event_id
from traceforge.models.events import LogEvent, normalize_severity
from traceforge.parsers.base import ParsedRecord, Parser, ParserContext, SourceLine

_TS_KEYS = ("timestamp", "time", "ts", "@timestamp", "datetime", "date", "event_time")
_LVL_KEYS = ("level", "severity", "log.level", "loglevel", "log_level", "levelname")
_MSG_KEYS = ("message", "msg", "log", "log.message", "event", "text")
_SVC_KEYS = ("service", "service_name", "service.name", "app", "application", "logger_name", "component")
_LOGGER_KEYS = ("logger", "logger_name", "logger.name")
_HOST_KEYS = ("host", "hostname", "host.name", "server", "server_name")
_PROC_KEYS = ("process", "process.name", "pid", "process_name")
_THREAD_KEYS = ("thread", "thread.name", "thread_name", "thread_id")
_TRACE_KEYS = ("trace_id", "traceId", "trace.id", "traceID")
_SPAN_KEYS = ("span_id", "spanId", "span.id", "spanID")
_PARENT_KEYS = ("parent_span_id", "parentSpanId", "parent_span", "parentSpan")
_REQUEST_KEYS = ("request_id", "requestId", "request.id", "requestID", "correlation_id", "correlationId")
_SESSION_KEYS = ("session_id", "sessionId", "session.id", "sessionID")
_DUR_KEYS = ("duration_ms", "duration", "latency_ms", "elapsed_ms", "response_time_ms")
_STATUS_KEYS = ("status_code", "status", "http.status_code", "http_status", "code")
_EXC_KEYS = ("exception_type", "exception", "error_type", "error.kind")

_KNOWN_TOP_LEVEL = frozenset(
    _TS_KEYS
    + _LVL_KEYS
    + _MSG_KEYS
    + _SVC_KEYS
    + _LOGGER_KEYS
    + _HOST_KEYS
    + _PROC_KEYS
    + _THREAD_KEYS
    + _TRACE_KEYS
    + _SPAN_KEYS
    + _PARENT_KEYS
    + _REQUEST_KEYS
    + _SESSION_KEYS
    + _DUR_KEYS
    + _STATUS_KEYS
    + _EXC_KEYS
)


def _first(obj: dict, keys: tuple[str, ...]) -> object | None:
    for k in keys:
        v = obj.get(k)
        if v is not None:
            return v
    for k in keys:
        if "." in k:
            cur: object = obj
            for part in k.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    cur = None
                    break
                cur = cur[part]
            if cur is not None:
                return cur
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if v > 1e15:
            return datetime.fromtimestamp(v / 1_000_000, tz=UTC)
        if v > 1e12:
            return datetime.fromtimestamp(v / 1000.0, tz=UTC)
        return datetime.fromtimestamp(v, tz=UTC)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _coerce_str(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _truncate_text(value: object, limit: int = 8000) -> str:
    s = str(value)
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _truncate_line(text: str, max_line_bytes: int) -> tuple[str, bool]:
    """Apply the per-line byte cap; return (text, was_truncated)."""
    if len(text) <= max_line_bytes:
        return text, False
    return text[:max_line_bytes] + "...[truncated]", True


class JsonLinesParser(Parser):
    name = "jsonl"

    def detect(self, sample_lines: list[str]) -> float:
        if not sample_lines:
            return 0.0
        ok = 0
        for line in sample_lines[:50]:
            s = line.strip()
            if not s:
                continue
            if s.startswith("{") and s.endswith("}"):
                try:
                    json.loads(s)
                    ok += 1
                except (ValueError, TypeError):
                    return 0.0
        return 1.0 if ok else 0.0

    def parse(
        self,
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        now = datetime.now(tz=UTC)
        for src in lines:
            text, was_truncated = _truncate_line(src.text, context.max_line_bytes)
            if not text.strip():
                continue
            raw = src.raw_bytes
            try:
                obj = json.loads(text)
            except (ValueError, TypeError):
                yield ParsedRecord(
                    event=self._unknown(now, context, src, text, raw, was_truncated),
                    unstructured=True,
                )
                continue
            if not isinstance(obj, dict):
                yield ParsedRecord(
                    event=self._unknown(now, context, src, text, raw, was_truncated),
                    unstructured=True,
                )
                continue
            ts = _parse_timestamp(_first(obj, _TS_KEYS))
            lvl = normalize_severity(str(_first(obj, _LVL_KEYS) or ""))
            msg_obj = _first(obj, _MSG_KEYS)
            message = _truncate_text(msg_obj if msg_obj is not None else text)
            attrs = {k: v for k, v in obj.items() if k not in _KNOWN_TOP_LEVEL}
            ev = LogEvent(
                event_id="",
                source=context.source_alias,
                source_path=context.source_path,
                line_number=src.line_number,
                timestamp=ts,
                ingested_at=now,
                severity=lvl,
                message=message,
                raw_text=text,
                raw_format="json",
                service=_coerce_str(_first(obj, _SVC_KEYS)),
                logger=_coerce_str(_first(obj, _LOGGER_KEYS)),
                host=_coerce_str(_first(obj, _HOST_KEYS)),
                process=_coerce_str(_first(obj, _PROC_KEYS)),
                thread=_coerce_str(_first(obj, _THREAD_KEYS)),
                trace_id=_coerce_str(_first(obj, _TRACE_KEYS)),
                span_id=_coerce_str(_first(obj, _SPAN_KEYS)),
                parent_span_id=_coerce_str(_first(obj, _PARENT_KEYS)),
                request_id=_coerce_str(_first(obj, _REQUEST_KEYS)),
                session_id=_coerce_str(_first(obj, _SESSION_KEYS)),
                duration_ms=_coerce_float(_first(obj, _DUR_KEYS)),
                status_code=_coerce_int(_first(obj, _STATUS_KEYS)),
                exception_type=_coerce_str(_first(obj, _EXC_KEYS)),
                attributes=attrs,
            )
            # The pipeline will fill byte_offset/line_number from the
            # SourceLine, but we already know them here; set them now so
            # the event is fully self-describing for tests.
            ev.byte_offset = src.byte_offset
            ev.line_number = src.line_number
            stamp_event_id(ev, raw, context.fingerprint_sample)
            yield ParsedRecord(event=ev, unstructured=False)

    def _unknown(
        self,
        now: datetime,
        context: ParserContext,
        src: SourceLine,
        text: str,
        raw: bytes,
        was_truncated: bool,
    ) -> LogEvent:
        msg = text[:8000]
        if was_truncated and "[truncated]" not in msg:
            msg = msg.rstrip() + "...[truncated]"
        ev = LogEvent(
            event_id="",
            source=context.source_alias,
            source_path=context.source_path,
            line_number=src.line_number,
            timestamp=None,
            ingested_at=now,
            severity="UNKNOWN",
            message=msg,
            raw_text=text,
            raw_format="json",
        )
        ev.byte_offset = src.byte_offset
        ev.line_number = src.line_number
        stamp_event_id(ev, raw, context.fingerprint_sample)
        return ev

    def is_multiline_capable(self) -> bool:
        return False


class JsonArrayParser(Parser):
    """Parses a single JSON array of objects, e.g. ``[ {...}, {...} ]``.

    **Memory note.** This parser buffers all source lines internally
    because the JSON-array format cannot be parsed incrementally without
    a full streaming-JSON dependency. Sources larger than
    :data:`MAX_JSON_ARRAY_BYTES` are refused outright (the parser
    returns no events and records a diagnostic). For very large
    datasets, convert the array to JSON Lines first.

    Source position: each parsed element is associated with the byte
    offset of the first ``{`` that introduces it in the source text
    (best-effort: arrays of large objects may have inaccurate byte
    positions if the array contains escaped braces inside strings).
    """

    name = "jsonarray"
    MAX_JSON_ARRAY_BYTES = 64 * 1024 * 1024  # 64 MiB hard cap

    def detect(self, sample_lines: list[str]) -> float:
        if not sample_lines:
            return 0.0
        s = "".join(sample_lines[:30]).strip()
        if not s.startswith("["):
            return 0.0
        try:
            v = json.loads(s)
        except (ValueError, TypeError):
            return 0.0
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return 0.95
        return 0.0

    def parse(
        self,
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        accumulated: list[SourceLine] = list(lines)
        if not accumulated:
            return
        total_bytes = sum(len(s.text) + 1 for s in accumulated)
        if total_bytes > self.MAX_JSON_ARRAY_BYTES:
            _record_diagnostic(
                context,
                f"jsonarray: source {context.source_path!r} is "
                f"{total_bytes} bytes which exceeds the hard cap of "
                f"{self.MAX_JSON_ARRAY_BYTES} bytes. Convert to JSONL "
                f"for large sources.",
            )
            return
        text = "".join(s.text + "\n" for s in accumulated)
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            _record_diagnostic(context, f"jsonarray: invalid JSON in {context.source_path!r}")
            return
        if not isinstance(obj, list):
            _record_diagnostic(
                context,
                f"jsonarray: top-level value is {type(obj).__name__}, not list",
            )
            return
        positions = _index_top_level_object_positions(text)
        inner = JsonLinesParser()
        line_offset = context.start_line
        for idx, item in enumerate(obj):
            if not isinstance(item, dict):
                continue
            try:
                serialized = json.dumps(item, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            byte_off = positions[idx] if idx < len(positions) else 0
            pseudo = SourceLine(
                byte_offset=byte_off,
                line_number=line_offset,
                text=serialized,
                raw_bytes=serialized.encode("utf-8"),
            )
            yield from inner.parse([pseudo], context)
            line_offset += 1


def _record_diagnostic(context: ParserContext, message: str) -> None:
    """Append a parser diagnostic to ``context`` without mutating its
    declared fields. The pipeline harvests the accumulated diagnostics
    on the way out and forwards them to ``IngestionProgress``.
    """
    bucket = getattr(context, "_parser_diagnostics", None)
    if bucket is None:
        bucket = []
        # Stash the bucket back on the context as an ad-hoc attribute.
        # This is a deliberate side-channel: ParserContext is a small
        # dataclass-like record and we don't want to add a real field
        # for an internal pipeline hook.
        try:
            object.__setattr__(context, "_parser_diagnostics", bucket)
        except Exception:
            pass
    if bucket is not None:
        bucket.append(message)


def _index_top_level_object_positions(text: str) -> list[int]:
    """Return approximate byte offsets for the start of each top-level
    object in a JSON array. This is best-effort and not a full parser."""
    positions: list[int] = []
    i = 0
    n = len(text)
    # skip leading whitespace and the opening '['
    while i < n and text[i] in " \t\r\n[":
        i += 1
    while i < n:
        if text[i] == "{":
            positions.append(i)
            depth = 1
            i += 1
            while i < n and depth > 0:
                c = text[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                elif c == '"':
                    # skip string
                    i += 1
                    while i < n and text[i] != '"':
                        if text[i] == "\\" and i + 1 < n:
                            i += 2
                        else:
                            i += 1
                i += 1
            # skip comma/whitespace
            while i < n and text[i] in " \t\r\n,":
                i += 1
        else:
            i += 1
    return positions
