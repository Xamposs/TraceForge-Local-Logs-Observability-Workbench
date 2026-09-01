# TFQL — TraceForge Query Language

TFQL is a small, safe, deterministic query language for the TraceForge
local event store. v0.1 supports filtering, comparison / set / string
operators, sorting, limits, and aggregations with grouping.

> **Safety:** TFQL values are **always** bound as DuckDB parameters. No
> user-controlled text is ever spliced into SQL syntax. See
> [`test_malicious_values_become_parameters`](../../tests/query/test_tfql.py).

## Grammar (v0.1)

```text
program     := expression ( PIPE stage )*
stage       := sort_stage | limit_stage | stats_stage
sort_stage  := SORT IDENT ( ASC | DESC )?
limit_stage := LIMIT NUMBER
stats_stage := STATS agg ( COMMA agg )* ( BY IDENT ( COMMA IDENT )* )?
agg         := agg_fn ( LPAREN IDENT RPAREN )? ( AS IDENT )?
agg_fn      := count | avg | min | max | sum | p50 | p95

expression  := or_expr
or_expr     := and_expr ( OR and_expr )*
and_expr    := not_expr ( AND not_expr )*
not_expr    := NOT not_expr | atom
atom        := LPAREN expression RPAREN
             | TRUE | FALSE | NULL
             | IDENT op value
             | IDENT IN LPAREN value ( COMMA value )* RPAREN
             | IDENT CONTAINS string
             | IDENT STARTS_WITH string
             | IDENT ENDS_WITH string
op          := = | != | > | >= | < | <=
value       := STRING | NUMBER | TRUE | FALSE | NULL | bare_ident
```

## Whitelisted fields

Only these column names are accepted in user queries; anything else
produces a clean compilation error with a suggestion:

| Alias | Canonical column |
| --- | --- |
| `level` | `severity` |
| `ts`, `time`, `@timestamp` | `timestamp` |
| `service.name` | `service` |
| `traceId` | `trace_id` |
| `spanId` | `span_id` |
| `requestId` | `request_id` |
| `sessionId` | `session_id` |
| `duration`, `latency_ms` | `duration_ms` |
| `status`, `code` | `status_code` |
| `raw` | `raw_text` |

Full canonical list:
`event_id`, `timestamp`, `ingested_at`, `severity`, `message`, `source`,
`source_path`, `line_number`, `byte_offset`, `service`, `logger`, `host`,
`process`, `thread`, `trace_id`, `span_id`, `parent_span_id`,
`request_id`, `session_id`, `duration_ms`, `status_code`,
`exception_type`, `raw_format`.

## Examples

```text
level = ERROR

level = ERROR AND service = "payments"

NOT level = DEBUG

duration_ms > 500 AND duration_ms < 5000

status_code >= 500

message CONTAINS "timeout"

message STARTS_WITH "Connection"

service IN ("api", "payments", "worker")

(level = ERROR OR level = FATAL) AND service = "auth"

duration_ms > 100
| sort duration_ms desc
| limit 50

level = ERROR
| stats count() by service

service = "payments"
| stats count(), avg(duration_ms), p95(duration_ms) by exception_type

| stats count()
```

## Error messages

Invalid queries raise a clean error with position / suggestion info, not
a stack trace:

```text
Unknown field: sevrice
Did you mean: service?

Unexpected token at position 18.

Unknown function: p999
```

## Limitations

- v0.1 only allows the whitelisted operators and functions.
- The parser is single-line; multi-line TFQL is not supported.
- The `count()` aggregate ignores its argument and counts all rows.
- `p50` and `p95` map to `quantile_cont(col, 0.50 / 0.95)`.
