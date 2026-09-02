"""Workspace persistence tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from traceforge.models.sources import SourceConfig
from traceforge.models.workspace import RuleConfig, SavedQuery
from traceforge.workspace_io import load_workspace, new_workspace, save_workspace


def test_new_workspace_default_state() -> None:
    ws = new_workspace("test")
    assert ws.name == "test"
    assert ws.sources == []
    assert ws.saved_queries == []
    assert ws.rules == []


def test_save_and_load(tmp_path: Path) -> None:
    ws = new_workspace("rt")
    ws.sources.append(SourceConfig(path="/var/log/app.log", alias="app"))
    ws.saved_queries.append(
        SavedQuery(
            name="errors",
            query="level = ERROR",
            created_at=datetime.now(tz=UTC),
        )
    )
    ws.rules.append(RuleConfig(name="spike", rule_id="error_rate_spike", enabled=True))
    p = tmp_path / "ws.trf"
    save_workspace(ws, p)
    assert p.exists()
    # Verify the file is valid JSON.
    parsed = json.loads(p.read_text(encoding="utf-8"))
    assert parsed["name"] == "rt"
    assert parsed["version"] == 2
    ws2 = load_workspace(p)
    assert ws2.sources[0].path == "/var/log/app.log"
    assert ws2.saved_queries[0].query == "level = ERROR"
    assert ws2.rules[0].rule_id == "error_rate_spike"


def test_load_invalid_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.trf"
    p.write_text("{not valid", encoding="utf-8")
    with pytest.raises(Exception):
        load_workspace(p)
