# Parsers

TraceForge auto-detects a source's format from a small sample, then runs
the appropriate parser. v0.1 includes these built-in parsers:

| Parser | Format | When it wins detection |
| --- | --- | --- |
| `JsonLinesParser` | JSONL / NDJSON | First non-empty line parses as a JSON object |
| `JsonArrayParser` | JSON array | First line starts with `[` and parses as a list of objects |
| `CsvLogParser` | CSV | First line has multiple commas and no `{` or `[` brackets |
| `ApacheAccessParser` | Apache / Nginx combined log | Starts with an IP and bracketed date |
| `NginxAccessParser` | (alias of Apache pattern) | Same heuristic as Apache |
| `CommonTextParser` | Generic text | ISO-8600 timestamp + level keyword, or bracketed timestamp |
| `GenericRegexParser` | User-supplied regex | Only when a custom regex is configured |

The detection function reads up to 64 KB from the file and scores each
parser by the fraction of sample lines that match its pattern. The
highest-scoring parser wins; ties are broken by parser order in the
registry.

## JSON normalisation

The JSON parsers recognise the following field-name aliases:

- **Time:** `timestamp`, `time`, `ts`, `@timestamp`, `datetime`, `date`, `event_time`
- **Severity:** `level`, `severity`, `log.level`, `loglevel`, `log_level`, `levelname`
- **Message:** `message`, `msg`, `log`, `log.message`, `event`, `text`
- **Service:** `service`, `service_name`, `service.name`, `app`, `application`, `logger_name`, `component`
- **Trace / span / request / session IDs:** `trace_id` / `traceId` / `trace.id`, etc.
- **Duration:** `duration_ms`, `duration`, `latency_ms`, `elapsed_ms`, `response_time_ms`
- **Status:** `status_code`, `status`, `http.status_code`, `http_status`, `code`
- **Exception:** `exception_type`, `exception`, `error_type`, `error.kind`

Unknown fields are preserved under `attributes: dict[str, Any]`. The CSV
parser uses the same field-name normalisation as JSON.

## Timestamps

`CommonTextParser` and `JsonLinesParser` understand a wide range of
timestamp formats. If a line lacks a timestamp, the event's `timestamp`
is left as `NULL`; the event is still ingested. We do not fabricate
metadata.

## Multiline folding

`CommonTextParser` uses a deterministic `MultilineAssembler` that buffers
lines until it sees a "primary" line (one matching any of these
patterns):

- ISO-8601 timestamp at the very start of the line
- Bracketed timestamp (`[2026-09-01 14:32:18]`)
- Apache / Nginx combined-log style (`127.0.0.1 - - [date]`)
- JSON object on a single line
- Syslog style (`Sep 01 14:32:18 host app:`)

Continuation lines (no recognised prefix) are folded onto the previous
primary line's message, with newlines preserved. A maximum event size of
200 KB protects against pathological continuations; oversized buffers
are truncated with a `...[truncated]` marker.

## Custom regex parser

The UI exposes a "Custom regex" option for advanced users. Provide a
regex with named groups and a map of `LogEvent` field names to those
group names. For example:

```text
Pattern:    ^(?P<timestamp>\d+)\s+(?P<level>\w+)\s+(?P<message>.*)$
Field map:  {"timestamp": "timestamp", "level": "level", "message": "message"}
```

Invalid regex is caught at the parser level and the input is kept as
`UNKNOWN` rather than silently dropped.

## Read-only

TraceForge never writes to source files. SHA-256 fingerprints of source
files are checked before and after ingestion in the test suite; the
property is also trivially enforced at runtime because the only paths
through the codebase open sources with `open(..., "rb")`.
