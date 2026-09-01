"""Parser tests covering JSONL, JSON arrays, CSV, text, multiline, malformed input."""

from __future__ import annotations

from traceforge.parsers import DEFAULT_REGISTRY, ParserContext
from traceforge.parsers.csv_parser import CsvLogParser
from traceforge.parsers.json_parser import JsonArrayParser, JsonLinesParser
from traceforge.parsers.multiline import MultilineAssembler, looks_like_primary
from traceforge.parsers.text_parser import CommonTextParser, GenericRegexParser


def _ctx(source_path: str = "memory") -> ParserContext:
    return ParserContext(source_path=source_path, source_alias="t", fingerprint_sample="abc")


def test_jsonl_basic() -> None:
    parser = JsonLinesParser()
    lines = [
        '{"timestamp":"2026-09-01T14:32:18Z","level":"ERROR","message":"boom","service":"api","trace_id":"abc"}',
    ]
    recs = list(parser.parse(lines, _ctx()))
    assert len(recs) == 1
    ev = recs[0].event
    assert ev.severity == "ERROR"
    assert ev.service == "api"
    assert ev.trace_id == "abc"
    assert ev.event_id.startswith("tfevt-")
    assert ev.timestamp is not None and ev.timestamp.year == 2026


def test_jsonl_malformed_is_kept_as_unknown() -> None:
    parser = JsonLinesParser()
    recs = list(parser.parse(["not json"], _ctx()))
    assert len(recs) == 1
    ev = recs[0].event
    assert ev.severity == "UNKNOWN"
    assert recs[0].unstructured is True


def test_jsonl_timestamp_millis() -> None:
    parser = JsonLinesParser()
    recs = list(
        parser.parse(
            ['{"timestamp":1756710738000,"message":"x"}'],
            _ctx(),
        )
    )
    assert recs[0].event.timestamp is not None


def test_json_array_parser() -> None:
    parser = JsonArrayParser()
    text = '[{"level":"INFO","message":"a"},{"level":"ERROR","message":"b"}]'
    recs = list(parser.parse([text], _ctx()))
    assert len(recs) == 2
    assert [r.event.severity for r in recs] == ["INFO", "ERROR"]


def test_csv_parser() -> None:
    parser = CsvLogParser()
    text = "timestamp,level,message\n2026-09-01,INFO,hi\n2026-09-01,ERROR,oh"
    recs = list(parser.parse(text.splitlines(), _ctx()))
    assert len(recs) == 2
    assert recs[0].event.severity == "INFO"
    assert recs[1].event.severity == "ERROR"


def test_csv_with_quoted_fields() -> None:
    parser = CsvLogParser()
    text = 'ts,message\n2026,"hello, world"\n2026,"line2"'
    recs = list(parser.parse(text.splitlines(), _ctx()))
    assert recs[0].event.message == "hello, world"
    assert recs[1].event.message == "line2"


def test_text_parser_iso_timestamp() -> None:
    parser = CommonTextParser()
    recs = list(parser.parse(["2026-09-01 14:32:18 ERROR [payments] request timeout"], _ctx()))
    assert recs[0].event.severity == "ERROR"
    assert recs[0].event.service == "payments"
    assert "request timeout" in recs[0].event.message


def test_text_parser_bracketed() -> None:
    parser = CommonTextParser()
    recs = list(parser.parse(["[2026-09-01 14:32:18] [ERROR] Something failed"], _ctx()))
    assert recs[0].event.severity == "ERROR"
    assert "Something failed" in recs[0].event.message


def test_text_parser_apache_log() -> None:
    parser = CommonTextParser()
    line = '127.0.0.1 - - [01/Sep/2026:14:32:18 +0000] "GET /api HTTP/1.1" 200 1234 "-" "curl"'
    recs = list(parser.parse([line], _ctx()))
    assert recs[0].event.severity == "UNKNOWN"  # no level keyword in apache log
    # Apache pattern matches but severity is UNKNOWN by default; just check
    # that a host / status were captured.
    assert recs[0].event.host == "127.0.0.1"
    assert recs[0].event.status_code == 200


def test_text_parser_unmatched_kept_as_unknown() -> None:
    parser = CommonTextParser()
    recs = list(parser.parse(["just a plain line"], _ctx()))
    assert recs[0].event.severity == "UNKNOWN"
    assert recs[0].event.message == "just a plain line"
    assert recs[0].unstructured is True


def test_multiline_assembler_folds_stack_traces() -> None:
    asm = MultilineAssembler()
    inputs = [
        "2026-09-01 ERROR request failed",
        "Traceback (most recent call last):",
        '  File "x.py", line 12, in foo',
        "ValueError: bad",
    ]
    out = []
    for line in inputs:
        for ev in asm.feed(line):
            out.append(ev)
    for ev in asm.flush():
        out.append(ev)
    assert len(out) == 1
    assert "Traceback" in out[0]
    assert "ValueError" in out[0]


def test_looks_like_primary_recognises_common_prefixes() -> None:
    assert looks_like_primary("2026-09-01 14:32:18 ERROR x")
    assert looks_like_primary("[2026-09-01 14:32:18] [ERROR] x")
    assert looks_like_primary('127.0.0.1 - - [01/Sep/2026:14:32:18 +0000] "GET /')
    assert looks_like_primary('{"timestamp":"x","message":"y"}')
    assert not looks_like_primary("    continuation line of stack")


def test_registry_detect() -> None:
    reg = DEFAULT_REGISTRY
    assert reg.detect(['{"timestamp":"x"}']).parser.name == "jsonl"
    assert reg.detect(['[{"a":1}]']).parser.name == "jsonarray"
    assert reg.detect(["2026-09-01 14:32:18 ERROR [payments] timeout"]).parser.name == "text"
    assert reg.detect(["a,b,c\n1,2,3"]).parser.name == "csv"


def test_registry_override() -> None:
    reg = DEFAULT_REGISTRY
    result = reg.detect(['{"a":1}'], override="text")
    assert result.parser.name == "text"


def test_custom_regex_parser() -> None:
    ctx = ParserContext(
        source_path="x",
        source_alias="x",
        custom_regex=r"^(?P<timestamp>\d+)\s+(?P<level>WARN|ERROR)\s+(?P<message>.*)$",
        custom_field_map={"timestamp": "timestamp", "level": "level", "message": "message"},
    )
    parser = GenericRegexParser()
    recs = list(parser.parse(["100 ERROR boom"], ctx))
    assert recs[0].event.severity == "ERROR"
    assert recs[0].event.message == "boom"


def test_custom_regex_parser_invalid_regex_falls_back() -> None:
    ctx = ParserContext(source_path="x", source_alias="x", custom_regex="[", custom_field_map={})
    parser = GenericRegexParser()
    recs = list(parser.parse(["foo"], ctx))
    assert recs[0].event.severity == "UNKNOWN"


def test_unicode_messages() -> None:
    parser = JsonLinesParser()
    recs = list(parser.parse(['{"level":"INFO","message":"héllo 🚀"}'], _ctx()))
    assert recs[0].event.message == "héllo 🚀"


def test_parser_handles_huge_line() -> None:
    parser = JsonLinesParser()
    huge = "x" * 2_000_000  # 2MB, beyond default cap
    recs = list(parser.parse([huge], _ctx()))
    # Falls back to UNKNOWN truncated text
    assert recs[0].event.severity == "UNKNOWN"
    assert "[truncated]" in recs[0].event.message
