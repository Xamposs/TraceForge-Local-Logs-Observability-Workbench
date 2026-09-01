"""JSON Lines / NDJSON parser."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

from traceforge.ingestion.ids import stamp_event_id
from traceforge.models.events import LogEvent, normalize_severity
from traceforge.parsers.base import ParsedRecord, Parser, ParserContext

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


def _first(obj: dict, keys: tuple[str, ...]) -> object | None:
    for k in keys:
        v = obj.get(k)
        if v is not None:
            return v
    # nested with dots
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
        # Heuristic: > 10^12 is millis, > 10^15 is micro, otherwise seconds.
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


def _truncate_text(value: object, limit: int = 8000) -> str:
    s = str(value)
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


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
        lines,
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        now = datetime.now(tz=UTC)
        start = max(1, context.start_line)
        for line_no, raw in enumerate(lines, start=start):
            if not raw:
                continue
            s = raw.rstrip("\n\r")
            if not s.strip():
                continue
            try:
                obj = json.loads(s)
            except (ValueError, TypeError):
                yield ParsedRecord(
                    event=self._unknown(now, context, line_no, s, raw),
                    unstructured=True,
                )
                continue
            if not isinstance(obj, dict):
                yield ParsedRecord(
                    event=self._unknown(now, context, line_no, s, raw),
                    unstructured=True,
                )
                continue
            ts = _parse_timestamp(_first(obj, _TS_KEYS))
            lvl = normalize_severity(str(_first(obj, _LVL_KEYS) or ""))
            msg_obj = _first(obj, _MSG_KEYS)
            message = _truncate_text(msg_obj if msg_obj is not None else s)
            attrs = {k: v for k, v in obj.items() if k not in _KNOWN_TOP_LEVEL}
            ev = LogEvent(
                event_id="",  # stamped below
                source=context.source_alias,
                source_path=context.source_path,
                line_number=line_no,
                timestamp=ts,
                ingested_at=now,
                severity=lvl,
                message=message,
                raw_text=s,
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
            stamp_event_id(ev, raw.encode("utf-8", errors="replace"), context.fingerprint_sample)
            yield ParsedRecord(event=ev, unstructured=False)

    def _unknown(self, now, context, line_no, s, raw):
        # Apply the per-line byte cap from the parser context, preserving a
        # visible truncation marker.
        truncated = s.endswith("...[truncated]")
        if not truncated and len(s) > context.max_line_bytes:
            s = s[: context.max_line_bytes] + "...[truncated]"
            truncated = True
        msg = s[:8000]
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
            raw_text=s,
            raw_format="json",
        )
        stamp_event_id(ev, raw.encode("utf-8", errors="replace"), context.fingerprint_sample)
        return ev


def _coerce_str(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v) -> int | None:
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


_KNOWN_TOP_LEVEL = set(
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


class JsonArrayParser(Parser):
    """Parses a single JSON array of objects, e.g. ``[ {...}, {...} ]``."""

    name = "jsonarray"

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

    def parse(self, lines, context: ParserContext):
        buf: list[str] = []
        for raw in lines:
            buf.append(raw)
        if not buf:
            return
        try:
            obj = json.loads("".join(buf))
        except (ValueError, TypeError):
            return
        if not isinstance(obj, list):
            return
        inner = JsonLinesParser()
        line_offset = 0
        for _idx, item in enumerate(obj, start=1):
            if not isinstance(item, dict):
                continue
            try:
                serialized = json.dumps(item, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            yield from inner.parse([serialized], _with_line_offset(context, line_offset + 1))
            line_offset += 1
        return


def _with_line_offset(ctx: ParserContext, offset: int) -> ParserContext:
    """Return a copy of the context with line numbers offset to maintain
    monotonicity when serializing JSON array elements as synthetic lines."""
    return ParserContext(
        source_path=ctx.source_path,
        source_alias=ctx.source_alias,
        fingerprint_sample=ctx.fingerprint_sample,
        max_line_bytes=ctx.max_line_bytes,
        custom_regex=ctx.custom_regex,
        custom_field_map=dict(ctx.custom_field_map) if ctx.custom_field_map else None,
        start_line=offset,
    )
