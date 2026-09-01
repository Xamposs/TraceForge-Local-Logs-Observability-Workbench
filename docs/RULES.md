# Deterministic Rules

TraceForge includes a small, fully deterministic rules engine. Every rule
is a pure-Python function that receives a `RuleContext` and returns a
list of `Alert` objects. **There is no AI, no LLM, no embedding.** All
decisions are reproducible from the events in the workspace.

## Built-in rules

### `error_rate_spike`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `window_min` | 5 | Length of the "recent" window in minutes |
| `baseline_min` | 30 | Length of the baseline window in minutes |
| `threshold_multiple` | 3.0 | Multiplier of baseline rate required to fire |
| `min_errors` | 20 | Minimum errors in the recent window to consider |
| `service` | (none) | Optional service filter |

Fires when:

```text
error_rate(recent) >= threshold_multiple * error_rate(baseline)
AND errors_in(recent) >= min_errors
```

### `new_error_signature`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `window_min` | 60 | Time window to look back for first occurrences |

Normalizes each error message (collapsing numbers, UUIDs, hex IDs,
timestamps, long identifiers) and fires the first time a previously
unseen signature appears in the workspace.

### `latency_threshold`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `window_min` | 5 | Time window in minutes |
| `threshold_ms` | 1000.0 | p95 latency threshold in milliseconds |
| `service` | (none) | Optional service filter |

Fires when `quantile_cont(duration_ms, 0.95)` over the window exceeds
the threshold for any service (with at least 5 events in the window).

### `event_burst`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `window_min` | 5 | Length of the "recent" window |
| `baseline_min` | 60 | Length of the baseline window |
| `factor` | 4.0 | Multiplier of baseline average required to fire |

Fires when the recent event count is at least `factor` times the
baseline average events per window.

### `missing_heartbeat`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `silence_min` | 10 | Minutes of silence required to fire |
| `service` | (none) | Optional service filter |

Fires for every service whose most recent event is older than `now -
silence_min`.

## Anatomy of an alert

Every alert carries a plain-language explanation, a threshold, an
observed value, and a time window. Example:

```text
Title:        Error rate spike in service payments
Severity:     WARNING
Fired at:     2026-09-01 14:32:18Z
Threshold:    3.0x baseline, >= 20 errors
Observed:     18.4% (37 errors / 201 events)
Time window:  5 min
Explanation:  Error rate in the last 5 minute(s) is 18.42%
              (baseline 3.12%). Triggered because current rate is
              at least 3.0x the baseline and there are at least 20
              errors in the window.
```

No criticality inflation. v0.1 deliberately uses INFO / NOTICE /
WARNING severities; a rule never claims "security incident" or "CRITICAL"
unless the user has explicitly configured that severity.

## Adding custom rules

A rule is just a function decorated with `@register(rule_id)`:

```python
from traceforge.rules import register, RuleContext
from traceforge.models.alerts import Alert
from datetime import datetime, timezone

@register("my_rule")
def my_rule(ctx: RuleContext) -> list[Alert]:
    # ... deterministic logic over ctx.db ...
    return []
```

Once registered, it can be referenced in `RuleConfig(name=..., rule_id="my_rule", ...)`
in a workspace. The UI exposes the rule's `name`, parameters, and enabled
flag.

## Anti-patterns

The rules engine will not:

- Load a model from disk.
- Make a network call.
- Synthesize English text beyond templated strings.
- Fire on "anything that looks unusual" — every alert has a threshold,
  an observed value, and a time window.

If a user wants AI-style anomaly detection, that is explicitly out of
scope for v0.1 and listed in the roadmap.
