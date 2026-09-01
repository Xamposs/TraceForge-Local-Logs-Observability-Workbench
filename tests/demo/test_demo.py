"""Demo generator tests."""

from __future__ import annotations

from pathlib import Path

from traceforge.demo import generate
from traceforge.demo.generator import DemoConfig


def test_demo_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "a", DemoConfig(event_count=1000, seed=42, duration_minutes=10, incident=False))
    generate(tmp_path / "b", DemoConfig(event_count=1000, seed=42, duration_minutes=10, incident=False))
    text_a = sorted(p.read_text(encoding="utf-8") for p in (tmp_path / "a").glob("*.log"))
    text_b = sorted(p.read_text(encoding="utf-8") for p in (tmp_path / "b").glob("*.log"))
    assert text_a == text_b


def test_demo_produces_all_services(tmp_path: Path) -> None:
    files = generate(tmp_path, DemoConfig(event_count=2000, seed=1, duration_minutes=10, incident=False))
    assert set(files.keys()) == {"gateway", "auth", "orders", "payments", "database"}


def test_demo_includes_incident_signatures(tmp_path: Path) -> None:
    generate(tmp_path, DemoConfig(event_count=5000, seed=7, duration_minutes=60, incident=True))
    text = (tmp_path / "payments.log").read_text(encoding="utf-8")
    assert "circuit breaker tripped" in text
