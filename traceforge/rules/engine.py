"""Deterministic rules engine.

A rule is a Python callable that takes a :class:`RuleContext` and returns
zero or more :class:`Alert` objects. Rules are configured via
:class:`RuleConfig` instances and executed in isolation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from traceforge.analytics.signatures import normalize_signature
from traceforge.models.alerts import Alert
from traceforge.models.workspace import RuleConfig
from traceforge.storage import Database


@dataclass
class RuleContext:
    db: Database
    config: RuleConfig
    workspace_id: str
    now: datetime


RuleFn = Callable[[RuleContext], list[Alert]]


_REGISTRY: dict[str, RuleFn] = {}


def register(rule_id: str):
    def deco(fn: RuleFn) -> RuleFn:
        _REGISTRY[rule_id] = fn
        return fn

    return deco


def available_rules() -> dict[str, str]:
    """Map rule_id -> short description (name from docstring)."""
    out = {}
    for rid, fn in _REGISTRY.items():
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else rid
        out[rid] = doc
    return out


def _new_id(*parts: Any) -> str:
    payload = "|".join(str(p) for p in parts)
    return "tfal-" + hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_pct(n: float, d: float) -> float:
    return float(n) / float(d) * 100.0 if d else 0.0


# ---- Rules ----


@register("error_rate_spike")
def error_rate_spike(ctx: RuleContext) -> list[Alert]:
    """Error rate in last N minutes vs. previous baseline window."""
    cfg = ctx.config
    window_min = int(cfg.parameters.get("window_min", 5))
    baseline_min = int(cfg.parameters.get("baseline_min", 30))
    threshold_x = float(cfg.parameters.get("threshold_multiple", 3.0))
    min_errors = int(cfg.parameters.get("min_errors", 20))
    service_filter = cfg.parameters.get("service")

    now = ctx.now
    win_start = now - timedelta(minutes=window_min)
    base_start = now - timedelta(minutes=baseline_min + window_min)
    base_end = now - timedelta(minutes=window_min)

    def counts(start: datetime, end: datetime) -> tuple[int, int]:
        where = ["timestamp >= ?", "timestamp < ?"]
        params: list[Any] = [start, end]
        if service_filter:
            where.append("service = ?")
            params.append(service_filter)
        sql = (
            "SELECT COUNT(*),"
            " SUM(CASE WHEN severity IN ('ERROR','FATAL','CRITICAL') THEN 1 ELSE 0 END)"
            " FROM events WHERE " + " AND ".join(where)
        )
        rel = ctx.db.execute(sql, params)
        row = rel.fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    total_now, errs_now = counts(win_start, now)
    total_base, errs_base = counts(base_start, base_end)
    if errs_now < min_errors:
        return []
    pct_now = _safe_pct(errs_now, total_now)
    pct_base = _safe_pct(errs_base, total_base)
    base_rate = pct_base / 100.0
    cur_rate = pct_now / 100.0
    if base_rate <= 0:
        return []
    if cur_rate < base_rate * threshold_x:
        return []
    return [
        Alert(
            id=_new_id("error_rate_spike", ctx.workspace_id, win_start.isoformat(), service_filter),
            rule_id="error_rate_spike",
            rule_name="Error rate spike",
            severity=cfg.severity,
            fired_at=now,
            title=("Error rate spike" + (f" in service {service_filter}" if service_filter else "")),
            explanation=(
                f"Error rate in the last {window_min} minute(s) is "
                f"{pct_now:.2f}% (baseline {pct_base:.2f}%). "
                f"Triggered because current rate is at least "
                f"{threshold_x:.1f}x the baseline and there are at least "
                f"{min_errors} errors in the window."
            ),
            threshold=f"{threshold_x:.1f}x baseline, >= {min_errors} errors",
            observed=f"{pct_now:.2f}% ({errs_now} errors / {total_now} events)",
            time_window=f"{window_min} min",
            scope={"service": service_filter} if service_filter else {},
        )
    ]


@register("new_error_signature")
def new_error_signature(ctx: RuleContext) -> list[Alert]:
    """Alert on the first appearance of a normalized error signature.

    Historical comparison is performed on the *normalized* signature,
    not the raw message. Two messages that differ only in dynamic
    values ("Connection timeout after 5012ms" vs "...4928ms") share
    the same signature and are treated as a single historical entry.
    """
    cfg = ctx.config
    window_min = int(cfg.parameters.get("window_min", 60))

    now = ctx.now
    start = now - timedelta(minutes=window_min)

    # Build the set of normalized signatures already known BEFORE the
    # window starts. We read the raw messages and normalize in Python
    # so the comparison matches the signature definition exactly.
    rel = ctx.db.execute(
        "SELECT message FROM events"
        " WHERE severity IN ('ERROR','FATAL','CRITICAL')"
        " AND timestamp IS NOT NULL AND timestamp < ?",
        [start],
    )
    known_signatures: set[str] = set()
    for row in rel.fetchall():
        msg = row[0]
        sig = normalize_signature(msg)
        if sig:
            known_signatures.add(sig)

    # In the window, look at each error message in arrival order.
    rel = ctx.db.execute(
        "SELECT message, MIN(timestamp) FROM events"
        " WHERE severity IN ('ERROR','FATAL','CRITICAL')"
        " AND timestamp >= ?"
        " GROUP BY message",
        [start],
    )
    alerts: list[Alert] = []
    seen_signatures: set[str] = set()
    for row in rel.fetchall():
        message = row[0]
        first_seen = row[1]
        sig = normalize_signature(message)
        if not sig or sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        if sig in known_signatures:
            continue
        alerts.append(
            Alert(
                id=_new_id(
                    "new_error_signature", ctx.workspace_id, sig, first_seen.isoformat() if first_seen else ""
                ),
                rule_id="new_error_signature",
                rule_name="New error signature",
                severity=cfg.severity,
                fired_at=now,
                title=f"New error signature: {sig[:80]}",
                explanation=(
                    f"A new normalized error signature has been observed for the first time. "
                    f"Signature: {sig}"
                ),
                threshold="First occurrence",
                observed=f"First seen at {first_seen.isoformat() if first_seen else 'unknown'}",
                time_window=f"{window_min} min",
                scope={"signature": sig},
            )
        )
        if len(alerts) >= 50:
            break
    return alerts


@register("latency_threshold")
def latency_threshold(ctx: RuleContext) -> list[Alert]:
    """Alert when p95(duration_ms) exceeds threshold in a service."""
    cfg = ctx.config
    window_min = int(cfg.parameters.get("window_min", 5))
    threshold_ms = float(cfg.parameters.get("threshold_ms", 1000.0))
    service_filter = cfg.parameters.get("service")

    now = ctx.now
    start = now - timedelta(minutes=window_min)
    where = ["timestamp >= ?", "duration_ms IS NOT NULL"]
    params: list[Any] = [start]
    if service_filter:
        where.append("service = ?")
        params.append(service_filter)
    sql = (
        "SELECT service, quantile_cont(duration_ms, 0.95) AS p95, COUNT(*) AS c"
        " FROM events WHERE " + " AND ".join(where) + " GROUP BY service"
    )
    rel = ctx.db.execute(sql, params)
    alerts: list[Alert] = []
    for row in rel.fetchall():
        service = row[0]
        p95 = float(row[1] or 0.0)
        count = int(row[2] or 0)
        if count < 5:
            continue
        if p95 < threshold_ms:
            continue
        alerts.append(
            Alert(
                id=_new_id("latency_threshold", ctx.workspace_id, service, start.isoformat()),
                rule_id="latency_threshold",
                rule_name="Latency threshold",
                severity=cfg.severity,
                fired_at=now,
                title=f"p95 latency above {threshold_ms:.0f}ms for {service or 'all services'}",
                explanation=(
                    f"p95 of duration_ms over the last {window_min} min is {p95:.1f}ms "
                    f"across {count} events."
                ),
                threshold=f"p95 > {threshold_ms:.0f}ms",
                observed=f"p95 = {p95:.1f}ms",
                time_window=f"{window_min} min",
                scope={"service": service},
            )
        )
    return alerts


@register("event_burst")
def event_burst(ctx: RuleContext) -> list[Alert]:
    """Detect a significant event-volume burst vs. previous rolling window."""
    cfg = ctx.config
    window_min = int(cfg.parameters.get("window_min", 5))
    baseline_min = int(cfg.parameters.get("baseline_min", 60))
    factor = float(cfg.parameters.get("factor", 4.0))

    now = ctx.now
    win_start = now - timedelta(minutes=window_min)
    base_start = now - timedelta(minutes=baseline_min + window_min)
    base_end = now - timedelta(minutes=window_min)

    rel = ctx.db.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp >= ? AND timestamp < ?",
        [win_start, now],
    )
    recent = int(rel.fetchone()[0] or 0)
    rel = ctx.db.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp >= ? AND timestamp < ?",
        [base_start, base_end],
    )
    base_total = int(rel.fetchone()[0] or 0)
    baseline_avg_per_window = (base_total * window_min) / max(1, baseline_min)
    if baseline_avg_per_window <= 0:
        return []
    if recent < baseline_avg_per_window * factor:
        return []
    return [
        Alert(
            id=_new_id("event_burst", ctx.workspace_id, win_start.isoformat()),
            rule_id="event_burst",
            rule_name="Event volume burst",
            severity=cfg.severity,
            fired_at=now,
            title="Event volume burst",
            explanation=(
                f"Recent window ({window_min} min) had {recent} events. "
                f"Baseline average over the prior {baseline_min} min was "
                f"{baseline_avg_per_window:.1f} events/window. "
                f"Current is at least {factor:.1f}x the baseline."
            ),
            threshold=f">= {factor:.1f}x baseline",
            observed=f"{recent} events vs baseline {baseline_avg_per_window:.1f}",
            time_window=f"{window_min} min vs {baseline_min} min",
        )
    ]


@register("missing_heartbeat")
def missing_heartbeat(ctx: RuleContext) -> list[Alert]:
    """Alert if no events have arrived from a service in N minutes.

    The check is performed over the service's overall ``MAX(timestamp)``,
    regardless of when that timestamp sits. A service with one recent
    event and one old event is considered alive (the recent one is the
    heartbeat).
    """
    cfg = ctx.config
    silence_min = int(cfg.parameters.get("silence_min", 10))
    service_filter = cfg.parameters.get("service")

    now = ctx.now
    cutoff = now - timedelta(minutes=silence_min)
    where = ["timestamp IS NOT NULL"]
    params: list[Any] = []
    if service_filter:
        where.append("service = ?")
        params.append(service_filter)
    sql = "SELECT service, MAX(timestamp) FROM events WHERE " + " AND ".join(where) + " GROUP BY service"
    rel = ctx.db.execute(sql, params)
    alerts: list[Alert] = []
    for row in rel.fetchall():
        service = row[0]
        last_ts = row[1]
        if last_ts is None:
            continue
        if last_ts >= cutoff:
            continue
        alerts.append(
            Alert(
                id=_new_id("missing_heartbeat", ctx.workspace_id, service, last_ts.isoformat()),
                rule_id="missing_heartbeat",
                rule_name="Missing heartbeat",
                severity=cfg.severity,
                fired_at=now,
                title=f"No events from {service} for {silence_min}+ min",
                explanation=(
                    f"Last event from {service} was at {last_ts.isoformat() if last_ts else 'unknown'}. "
                    f"Threshold is {silence_min} minutes of silence."
                ),
                threshold=f"silence > {silence_min} min",
                observed=f"last seen at {last_ts.isoformat() if last_ts else 'unknown'}",
                time_window=f"{silence_min} min",
                scope={"service": service},
            )
        )
    return alerts


def run_all(
    db: Database,
    rules: list[RuleConfig],
    *,
    workspace_id: str,
    now: datetime | None = None,
) -> list[Alert]:
    now = now or datetime.now(tz=UTC)
    out: list[Alert] = []
    for cfg in rules:
        if not cfg.enabled:
            continue
        fn = _REGISTRY.get(cfg.rule_id)
        if fn is None:
            continue
        try:
            ctx = RuleContext(db=db, config=cfg, workspace_id=workspace_id, now=now)
            out.extend(fn(ctx))
        except Exception:
            # Never let a rule take down the engine.
            continue
    return out


def default_rules() -> list[RuleConfig]:
    from traceforge.models.workspace import RuleConfig

    return [
        RuleConfig(
            name="Error rate spike",
            rule_id="error_rate_spike",
            enabled=False,
            severity="WARNING",
            parameters={"window_min": 5, "baseline_min": 30, "threshold_multiple": 3.0, "min_errors": 20},
        ),
        RuleConfig(
            name="New error signature",
            rule_id="new_error_signature",
            enabled=True,
            severity="NOTICE",
            parameters={"window_min": 60},
        ),
        RuleConfig(
            name="Latency threshold (default 1000ms)",
            rule_id="latency_threshold",
            enabled=False,
            severity="WARNING",
            parameters={"window_min": 5, "threshold_ms": 1000.0},
        ),
        RuleConfig(
            name="Event volume burst",
            rule_id="event_burst",
            enabled=False,
            severity="NOTICE",
            parameters={"window_min": 5, "baseline_min": 60, "factor": 4.0},
        ),
        RuleConfig(
            name="Missing heartbeat",
            rule_id="missing_heartbeat",
            enabled=False,
            severity="INFO",
            parameters={"silence_min": 10},
        ),
    ]
