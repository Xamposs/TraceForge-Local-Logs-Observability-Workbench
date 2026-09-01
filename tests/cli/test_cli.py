"""CLI tests using a small in-process temporary workspace."""

from __future__ import annotations

import json
from pathlib import Path

from traceforge import cli


def test_version_command() -> None:
    rc = cli.main(["version"])
    assert rc == 0


def test_validate_query_ok() -> None:
    rc = cli.main(["validate-query", "level = ERROR"])
    assert rc == 0


def test_validate_query_fail() -> None:
    rc = cli.main(["validate-query", "unknown = ERROR"])
    assert rc == 1


def test_validate_query_json() -> None:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["--json", "validate-query", "level = ERROR"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is True


def test_generate_demo(temp_dir: Path) -> None:
    target = temp_dir / "demo"
    rc = cli.main(["generate-demo", str(target), "--events", "1000", "--duration-minutes", "30"])
    assert rc == 0
    files = list(target.glob("*.log"))
    assert len(files) == 5


def test_query_against_demo(temp_dir: Path) -> None:
    target = temp_dir / "demo2"
    cli.main(["generate-demo", str(target), "--events", "1000", "--duration-minutes", "30"])
    rc = cli.main(["query", str(target), "level = ERROR | limit 5"])
    assert rc == 0


def test_inspect_missing_path() -> None:
    rc = cli.main(["inspect", "does-not-exist.log"])
    assert rc == 2
