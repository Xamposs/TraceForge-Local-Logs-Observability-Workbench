"""Command-line interface for TraceForge.

The CLI reuses the same parser / query / storage modules as the GUI; it does
not duplicate logic. The supported subcommands are:

* ``traceforge inspect <path>``      - quick file inspection
* ``traceforge query <path> <tfql>`` - run a TFQL query against a source
* ``traceforge validate-query <tfql>``- validate a TFQL string
* ``traceforge generate-demo <dir>`` - create deterministic demo logs
* ``traceforge launch``              - launch the desktop GUI
* ``traceforge version``             - print version
* ``traceforge workspace-info <.trf>`` - print a workspace summary
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from traceforge import __version__
from traceforge.demo import DemoConfig
from traceforge.demo import generate as generate_demo
from traceforge.ingestion.pipeline import ingest_file
from traceforge.models.sources import SourceConfig
from traceforge.query import QueryError, execute, validate
from traceforge.storage import Database, EventRepository
from traceforge.workspace_io import load_workspace


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="traceforge",
        description="TraceForge — local logs and observability workbench.",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    sub = p.add_subparsers(dest="cmd", required=False)
    sub.add_parser("version", help="Print version and exit.")
    p_inspect = sub.add_parser("inspect", help="Inspect a single log file and print a quick summary.")
    p_inspect.add_argument("path")
    p_inspect.add_argument("--limit", type=int, default=20)
    p_query = sub.add_parser("query", help="Ingest a path then run a TFQL query.")
    p_query.add_argument("path")
    p_query.add_argument("query")
    p_query.add_argument("--limit", type=int, default=1000)
    p_validate = sub.add_parser("validate-query", help="Parse and compile a TFQL query without executing.")
    p_validate.add_argument("query")
    p_demo = sub.add_parser("generate-demo", help="Generate a deterministic demo log set.")
    p_demo.add_argument("target", nargs="?", default="./demo-logs")
    p_demo.add_argument("--events", type=int, default=50_000)
    p_demo.add_argument("--seed", type=int, default=20260901)
    p_demo.add_argument("--duration-minutes", type=int, default=240)
    p_demo.add_argument("--no-incident", action="store_true")
    p_ws = sub.add_parser("workspace-info", help="Print a summary of a .trf workspace file.")
    p_ws.add_argument("path")
    sub.add_parser("launch", help="Launch the desktop GUI (default if no command).")
    return p


def _emit(payload: Any, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return
    if isinstance(payload, list):
        for item in payload:
            print(item)
        return
    if isinstance(payload, dict):
        for k, v in payload.items():
            print(f"{k}: {v}")
        return
    print(payload)


def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: path not found: {path}", file=sys.stderr)
        return 2
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
    else:
        files = [path]
    summary: dict[str, Any] = {"files": []}
    for f in files[:20]:
        try:
            st = f.stat()
            summary["files"].append({"path": str(f), "size": st.st_size, "mtime_ns": st.st_mtime_ns})
        except OSError as e:
            summary["files"].append({"path": str(f), "error": str(e)})
    summary["file_count"] = len(files)
    if args.json:
        _emit(summary, json_mode=True)
    else:
        print(f"Found {len(files)} file(s). Showing up to 20:")
        for item in summary["files"]:
            print(f"  {item['path']}  ({item.get('size','?')} bytes)")
    # Show a small sample of events for the first file
    if files:
        tmp = Path(tempfile.mkdtemp(prefix="traceforge-cli-"))
        db_path = tmp / "session.duckdb"
        db = Database(db_path)
        cfg = SourceConfig(path=str(files[0]), alias=str(files[0]))
        try:
            result = ingest_file(db, cfg)
        except Exception as e:
            print(f"ingest error: {e}", file=sys.stderr)
            db.close()
            return 1
        repo = EventRepository(db)
        sev = repo.count_by_severity()
        rel = db.execute(
            "SELECT timestamp, severity, service, message FROM events ORDER BY timestamp LIMIT ?",
            [args.limit],
        )
        rows = rel.fetchall()
        if args.json:
            _emit(
                {
                    "first_file": str(files[0]),
                    "parser": result.parser_name,
                    "events": result.progress.events_parsed,
                    "severity": sev,
                    "sample": [
                        {
                            "timestamp": r[0].isoformat() if r[0] else None,
                            "severity": r[1],
                            "service": r[2],
                            "message": r[3],
                        }
                        for r in rows
                    ],
                },
                json_mode=True,
            )
        else:
            print()
            print(f"Parser: {result.parser_name}")
            print(f"Events: {result.progress.events_parsed}")
            print("Severity counts:")
            for k, v in sorted(sev.items(), key=lambda x: -x[1]):
                print(f"  {k:10s} {v}")
            print()
            print(f"First {len(rows)} events:")
            for r in rows:
                ts = r[0].isoformat(sep=" ") if r[0] else "-"
                print(f"  {ts}  {r[1]:8s}  {(r[2] or '-'):10s}  {r[3]}")
        db.close()
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: path not found: {path}", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="traceforge-cli-"))
    db_path = tmp / "session.duckdb"
    db = Database(db_path)
    try:
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file())
        else:
            files = [path]
        for f in files:
            cfg = SourceConfig(path=str(f), alias=str(f))
            ingest_file(db, cfg)
        try:
            result = execute(db, args.query, base_limit=args.limit)
        except QueryError as e:
            print(f"query error: {e}", file=sys.stderr)
            return 3
        if args.json:
            _emit(
                {
                    "columns": result.columns,
                    "rows": [list(r) for r in result.rows],
                    "row_count": result.row_count,
                    "elapsed_ms": result.elapsed_ms,
                    "is_aggregation": result.is_aggregation,
                },
                json_mode=True,
            )
        else:
            print(f"{result.row_count} row(s) in {result.elapsed_ms:.1f} ms")
            if not result.rows:
                return 0
            widths = [min(40, max(len(c), 4)) for c in result.columns]
            print("  ".join(c.ljust(w) for c, w in zip(result.columns, widths, strict=False)))
            print("-" * (sum(widths) + 2 * (len(widths) - 1)))
            for r in result.rows:
                cells = []
                for v, w in zip(r, widths, strict=False):
                    s = "" if v is None else str(v)
                    if len(s) > w:
                        s = s[: w - 1] + "…"
                    cells.append(s.ljust(w))
                print("  ".join(cells))
    finally:
        db.close()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ok, msg = validate(args.query)
    if args.json:
        _emit({"ok": ok, "message": msg}, json_mode=True)
    else:
        print("OK" if ok else f"FAIL: {msg}")
    return 0 if ok else 1


def cmd_generate_demo(args: argparse.Namespace) -> int:
    cfg = DemoConfig(
        event_count=args.events,
        seed=args.seed,
        duration_minutes=args.duration_minutes,
        incident=not args.no_incident,
    )
    out = generate_demo(args.target, cfg)
    if args.json:
        _emit({"event_count": cfg.event_count, "files": out}, json_mode=True)
    else:
        print(f"Generated {cfg.event_count} events into {len(out)} files under {args.target}")
        for k, v in out.items():
            print(f"  {k}: {v}")
    return 0


def cmd_workspace_info(args: argparse.Namespace) -> int:
    try:
        ws = load_workspace(args.path)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    payload = {
        "version": ws.version,
        "name": ws.name,
        "created_at": ws.created_at.isoformat(),
        "updated_at": ws.updated_at.isoformat(),
        "sources": [s.model_dump(mode="json") for s in ws.sources],
        "saved_queries": [q.model_dump(mode="json") for q in ws.saved_queries],
        "rules": [r.model_dump(mode="json") for r in ws.rules],
    }
    if args.json:
        _emit(payload, json_mode=True)
    else:
        print(f"Workspace: {ws.name} (v{ws.version})")
        print(f"Created:   {ws.created_at.isoformat()}")
        print(f"Updated:   {ws.updated_at.isoformat()}")
        print(f"Sources:   {len(ws.sources)}")
        for s in ws.sources:
            print(f"  - {s.alias or s.path}")
        print(f"Queries:   {len(ws.saved_queries)}")
        for q in ws.saved_queries:
            print(f"  - {q.name}: {q.query}")
        print(f"Rules:     {len(ws.rules)}")
        for r in ws.rules:
            print(f"  - {r.name} ({'on' if r.enabled else 'off'})")
    return 0


def cmd_launch(_: argparse.Namespace) -> int:
    from traceforge.ui.app import launch

    launch()
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    if _.json if False else False:  # never
        _emit({"version": __version__}, json_mode=True)
    else:
        print(f"traceforge {__version__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        # Default: launch GUI
        try:
            from traceforge.ui.app import launch
        except Exception as e:
            print(f"Failed to launch GUI: {e}", file=sys.stderr)
            return 1
        launch()
        return 0
    dispatch = {
        "version": cmd_version,
        "inspect": cmd_inspect,
        "query": cmd_query,
        "validate-query": cmd_validate,
        "generate-demo": cmd_generate_demo,
        "workspace-info": cmd_workspace_info,
        "launch": cmd_launch,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
