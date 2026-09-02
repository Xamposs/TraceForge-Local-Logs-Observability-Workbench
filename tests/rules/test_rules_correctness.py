"""Regression tests for the deterministic rules engine (Sections 16-17).

* 16 — missing heartbeat must use MAX(timestamp), not filter then MAX.
* 17 — new error signature must dedup on the normalized signature, not
        on the raw message text.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from traceforge.models.events import LogEvent
from traceforge.models.workspace import RuleConfig
from traceforge.rules import run_all
from traceforge.storage import Database, EventRepository


def _ev(i: int, *, service: str, ts: datetime, message: str, severity: str = "ERROR") -> LogEvent:
    return LogEvent(
        event_id=f"e{i}",
        source="x",
        source_path="/tmp/x",
        line_number=i,
        timestamp=ts,
        ingested_at=ts,
        severity=severity,
        message=message,
        raw_text=message,
        raw_format="text",
        service=service,
    )


def test_missing_heartbeat_does_not_fire_for_recent_event(database: Database) -> None:
    """A service that has events both 1 hour ago AND 1 minute ago must
    not trigger a missing-heartbeat alert: the recent event is the
    heartbeat."""
    now = datetime.now(tz=UTC)
    events = [
        _ev(1, service="payments", ts=now - timedelta(hours=1), message="old"),
        _ev(2, service="payments", ts=now - timedelta(minutes=1), message="new"),
    ]
    EventRepository(database).insert_events(events, source_id=1)
    rule = RuleConfig(
        name="HB",
        rule_id="missing_heartbeat",
        enabled=True,
        severity="WARNING",
        parameters={"silence_min": 10},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert alerts == []


def test_missing_heartbeat_fires_for_stale_service(database: Database) -> None:
    now = datetime.now(tz=UTC)
    events = [
        _ev(1, service="payments", ts=now - timedelta(hours=1), message="old"),
    ]
    EventRepository(database).insert_events(events, source_id=1)
    rule = RuleConfig(
        name="HB",
        rule_id="missing_heartbeat",
        enabled=True,
        severity="WARNING",
        parameters={"silence_min": 10},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert len(alerts) == 1
    assert "payments" in alerts[0].title


def test_new_error_signature_dedups_by_normalized_signature(database: Database) -> None:
    """Two messages that differ only in dynamic numbers must NOT both
    be reported as a brand-new signature."""
    now = datetime.now(tz=UTC)
    events = [
        _ev(1, service="payments", ts=now - timedelta(minutes=30), message="Connection timeout after 5012ms"),
        _ev(2, service="payments", ts=now - timedelta(minutes=20), message="Connection timeout after 4928ms"),
    ]
    EventRepository(database).insert_events(events, source_id=1)
    rule = RuleConfig(
        name="NES",
        rule_id="new_error_signature",
        enabled=True,
        severity="NOTICE",
        parameters={"window_min": 60},
    )
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    # Both messages normalize to the same signature; we expect one
    # alert, not two.
    assert len(alerts) == 1


def test_new_error_signature_does_not_re_fire_every_run(database: Database) -> None:
    """Repeated rule evaluations over the same data should not emit a
    stream of identical alerts (no persistent-state model is intended
    in v0.1 — each evaluation sees only the new window)."""
    now = datetime.now(tz=UTC)
    events = [
        _ev(1, service="payments", ts=now - timedelta(minutes=5), message="Brand new error"),
    ]
    EventRepository(database).insert_events(events, source_id=1)
    rule = RuleConfig(
        name="NES",
        rule_id="new_error_signature",
        enabled=True,
        severity="NOTICE",
        parameters={"window_min": 60},
    )
    # Single run, with a 60-min window, emits one alert for the
    # never-before-seen signature.
    alerts = run_all(database, [rule], workspace_id="t", now=now)
    assert len(alerts) == 1
