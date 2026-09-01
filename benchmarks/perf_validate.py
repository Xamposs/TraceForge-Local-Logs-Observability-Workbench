"""Performance / scale validation.

Generates a 100k event dataset, ingests it, and measures:
- ingestion time
- basic query latency
- storage size

This script is informational; do not commit generated data.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    work_dir = Path("./perf")
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "demo"
    target.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "perf.duckdb"
    if db_path.exists():
        db_path.unlink()
    ws_dir = work_dir / "ws"
    if ws_dir.exists():
        import shutil

        shutil.rmtree(ws_dir)
    ws_dir.mkdir()

    # Generate logs.
    from traceforge.demo import DemoConfig, generate

    print(f"Generating {n} events...")
    t0 = time.perf_counter()
    files = generate(target, DemoConfig(event_count=n, seed=42, duration_minutes=120))
    print(f"  generation: {time.perf_counter() - t0:.2f}s; {len(files)} files")

    # Ingest.
    from traceforge.ingestion.pipeline import ingest_file
    from traceforge.models.sources import SourceConfig
    from traceforge.storage import Database, EventRepository

    db = Database(db_path)
    repo = EventRepository(db)
    sources = list(files.values())
    print(f"Ingesting {len(sources)} sources...")
    t0 = time.perf_counter()
    total_events = 0
    for path in sources:
        cfg = SourceConfig(path=path, alias=Path(path).name)
        res = ingest_file(db, cfg)
        total_events += res.progress.events_parsed
    elapsed = time.perf_counter() - t0
    print(f"  ingestion: {elapsed:.2f}s, {total_events} events, {total_events / elapsed:.0f} events/s")
    print(f"  DB size: {db_path.stat().st_size:,} bytes")

    # Query latency.
    from traceforge.query import execute

    print("Querying...")
    for q in [
        "severity = ERROR",
        'service = "payments"',
        "duration_ms > 100 | sort timestamp desc | limit 100",
    ]:
        t0 = time.perf_counter()
        r = execute(db, q)
        qe = time.perf_counter() - t0
        print(f"  {q!r:60s} -> {r.row_count} rows in {qe*1000:.1f} ms")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
