"""CSV log parser.

Streams the file line-by-line through Python's :mod:`csv` reader, so
the entire source is never materialised as one large Python string. The
header row is consumed first; each subsequent data row is converted
into a :class:`LogEvent` whose source position is the byte offset of
the first byte of that data row in the original file.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

from traceforge.ingestion.ids import stamp_event_id
from traceforge.models.events import LogEvent, normalize_severity
from traceforge.parsers.base import ParsedRecord, Parser, ParserContext, SourceLine
from traceforge.parsers.json_parser import (
    _DUR_KEYS,
    _EXC_KEYS,
    _HOST_KEYS,
    _KNOWN_TOP_LEVEL,
    _LOGGER_KEYS,
    _LVL_KEYS,
    _MSG_KEYS,
    _PARENT_KEYS,
    _PROC_KEYS,
    _REQUEST_KEYS,
    _SESSION_KEYS,
    _SPAN_KEYS,
    _STATUS_KEYS,
    _SVC_KEYS,
    _THREAD_KEYS,
    _TRACE_KEYS,
    _TS_KEYS,
    _coerce_float,
    _coerce_int,
    _coerce_str,
    _first,
    _parse_timestamp,
)


class CsvLogParser(Parser):
    name = "csv"

    def detect(self, sample_lines: list[str]) -> float:
        if not sample_lines:
            return 0.0
        first = sample_lines[0].strip()
        if not first or first.startswith("{") or first.startswith("["):
            return 0.0
        if first.count(",") < 2:
            return 0.0
        try:
            rdr = csv.reader(io.StringIO("\n".join(sample_lines[:5])))
            rows = list(rdr)
        except (csv.Error, ValueError):
            return 0.0
        if not rows:
            return 0.0
        header = rows[0]
        if len(header) < 2:
            return 0.0
        return 0.7

    def parse(
        self,
        lines: Iterable[SourceLine],
        context: ParserContext,
    ) -> Iterator[ParsedRecord | None]:
        now = datetime.now(tz=UTC)
        header: list[str] | None = None
        for src in lines:
            # Use the per-line text but feed through csv.reader.
            if header is None:
                rdr = csv.reader(io.StringIO(src.text))
                try:
                    header = next(rdr)
                except StopIteration:
                    continue
                continue
            row = next(csv.reader(io.StringIO(src.text)), None)
            if row is None:
                continue
            yield ParsedRecord(
                event=self._row_to_event(row, header, src, now, context),
                unstructured=False,
            )

    def _row_to_event(
        self,
        row: list[str],
        header: list[str],
        src: SourceLine,
        now: datetime,
        context: ParserContext,
    ) -> LogEvent:
        obj: dict[str, object] = {}
        for i, key in enumerate(header):
            if i < len(row):
                obj[key] = row[i]
            else:
                obj[key] = None
        ts = _parse_timestamp(_first(obj, _TS_KEYS))
        lvl = normalize_severity(str(_first(obj, _LVL_KEYS) or ""))
        msg_obj = _first(obj, _MSG_KEYS)
        message = (str(msg_obj) if msg_obj is not None else ",".join(row))[:8000]
        attrs = {k: v for k, v in obj.items() if k not in _KNOWN_TOP_LEVEL}
        raw_text = ",".join(row)
        ev = LogEvent(
            event_id="",
            source=context.source_alias,
            source_path=context.source_path,
            line_number=src.line_number,
            timestamp=ts,
            ingested_at=now,
            severity=lvl,
            message=message,
            raw_text=raw_text,
            raw_format="csv",
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
        ev.byte_offset = src.byte_offset
        ev.line_number = src.line_number
        stamp_event_id(ev, raw_text.encode("utf-8", errors="replace"), context.fingerprint_sample)
        return ev

    def is_multiline_capable(self) -> bool:
        return False
