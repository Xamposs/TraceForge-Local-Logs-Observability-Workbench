"""Rule engine tests covering each built-in deterministic rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.models.workspace import RuleConfig
from traceforge.rules import run_all
from traceforge.storage import Database, EventRepository


def _event(
    i: int,
    *,
    severity: str = "INFO",
    service: str = "api",
    message: str = "msg",
    duration_ms: float | None = None,
    ts: datetime | None = None,
    trace_id: str | None = None,
) -> LogEvent:
    return LogEvent(
        event_id=f"e{i}",
        source="x",
        source_path="/tmp/x",
        line_number=i + 1,
        timestamp=ts,
        ingested_at=datetime.now(tz=UTC),
        severity=severity,
        message=message,
        raw_text="",
        service=service,
        duration_ms=duration_ms,
        trace_id=trace_id,
        raw_format="text",
    )


def _seed(database: Database, events: list[LogEvent]) -> None:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/x", alias="x")
    fp = SourceFingerprint(path="/x", size=0, mtime_ns=0, sample_hash="h", content_kind="text")
    stats = SourceStats(path="/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    repo.insert_events(events, sid)


def test_new_error_signature(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [_event(1, severity="ERROR", message="Something failed", ts=now - timedelta(minutes=5))]
    _seed(database, events)
    rule = RuleConfig(name="New", rule_id="new_error_signature", enabled=True, parameters={"window_min": 60})
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert any(a.rule_id == "new_error_signature" for a in alerts)


def test_new_error_signature_dedup(database: Database) -> None:
    now = datetime.now(tz=UTC)
    # Same message that appeared 2h ago should NOT trigger again.
    events = [
        _event(1, severity="ERROR", message="Boom", ts=now - timedelta(hours=2)),
        _event(2, severity="ERROR", message="Boom", ts=now - timedelta(minutes=5)),
    ]
    _seed(database, events)
    rule = RuleConfig(name="New", rule_id="new_error_signature", enabled=True, parameters={"window_min": 60})
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert alerts == []


def test_error_rate_spike(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = []
    # baseline: ~5% errors in the prior 30 minutes.
    for i in range(300):
        sev = "ERROR" if (i % 20 == 0) else "INFO"
        events.append(_event(i, severity=sev, ts=now - timedelta(minutes=10 + i // 10)))
    # recent: 50 errors in last 1 minute.
    for i in range(50):
        events.append(_event(1000 + i, severity="ERROR", ts=now - timedelta(minutes=1)))
    _seed(database, events)
    rule = RuleConfig(
        name="Spike",
        rule_id="error_rate_spike",
        enabled=True,
        parameters={"window_min": 5, "baseline_min": 30, "threshold_multiple": 3.0, "min_errors": 20},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert any(a.rule_id == "error_rate_spike" for a in alerts)


def test_error_rate_spike_no_alert_when_too_few(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [_event(i, severity="INFO", ts=now - timedelta(minutes=40)) for i in range(20)]
    _seed(database, events)
    rule = RuleConfig(
        name="Spike",
        rule_id="error_rate_spike",
        enabled=True,
        parameters={"window_min": 5, "baseline_min": 30, "min_errors": 1000},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert alerts == []


def test_latency_threshold(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        _event(i, service="payments", duration_ms=1500.0, ts=now - timedelta(minutes=1)) for i in range(20)
    ]
    _seed(database, events)
    rule = RuleConfig(
        name="Lat",
        rule_id="latency_threshold",
        enabled=True,
        parameters={"window_min": 5, "threshold_ms": 1000.0},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert any(a.rule_id == "latency_threshold" for a in alerts)


def test_event_burst(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = []
    for i in range(20):
        events.append(_event(i, ts=now - timedelta(minutes=20 + i // 5)))
    for i in range(200):
        events.append(_event(1000 + i, ts=now - timedelta(seconds=10)))
    _seed(database, events)
    rule = RuleConfig(
        name="Burst",
        rule_id="event_burst",
        enabled=True,
        parameters={"window_min": 5, "baseline_min": 60, "factor": 4.0},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert any(a.rule_id == "event_burst" for a in alerts)


def test_missing_heartbeat(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        _event(1, service="payments", ts=now - timedelta(minutes=20)),
        _event(2, service="api", ts=now - timedelta(minutes=1)),
    ]
    _seed(database, events)
    rule = RuleConfig(name="Heart", rule_id="missing_heartbeat", enabled=True, parameters={"silence_min": 10})
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    # payments should fire; api should not.
    titles = [a.title for a in alerts]
    assert any("payments" in t for t in titles)
    assert not any("api " in t for t in titles)


def test_disabled_rule_does_not_fire(database: Database) -> None:
    now = datetime.now(tz=UTC)
    _seed(database, [_event(1, severity="ERROR", ts=now - timedelta(minutes=5))])
    rule = RuleConfig(name="Off", rule_id="new_error_signature", enabled=False)
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert alerts == []


def test_unknown_rule_id_is_ignored(database: Database) -> None:
    alerts = run_all(database, [RuleConfig(name="x", rule_id="not-a-rule")], workspace_id="t")
    assert alerts == []
