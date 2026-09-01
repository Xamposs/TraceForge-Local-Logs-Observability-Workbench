"""Deterministic synthetic log generator.

Produces a realistic microservice scenario with five services, request/trace
IDs, durations, status codes, multiline stack traces, and three intentional
incident patterns:

1. A payment latency spike around the middle of the dataset.
2. A new error signature appearing mid-dataset.
3. An error-rate burst toward the end.

All output is deterministic for a given (seed, event_count).
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SERVICES = ("gateway", "auth", "orders", "payments", "database")
HOSTS = ("web-01", "web-02", "worker-01", "db-01")
LOGGERS = {
    "gateway": "gateway.http",
    "auth": "auth.service",
    "orders": "orders.api",
    "payments": "payments.processor",
    "database": "db.driver",
}
PATHS = (
    "/api/orders",
    "/api/payments",
    "/api/login",
    "/api/users",
    "/api/products",
    "/api/cart",
    "/api/checkout",
)
PAYMENT_ERRORS = (
    "Card declined: insufficient funds",
    "Payment provider timeout after 5012ms",
    "Card declined: fraud suspicion",
    "Payment provider 5xx",
)
AUTH_ERRORS = (
    "Invalid token signature",
    "Token expired",
    "User not found",
)
ORDERS_ERRORS = (
    "Inventory check failed: SKU not found",
    "Order validation failed: missing field",
)
GENERIC_WARN = (
    "Slow query detected: took 742ms",
    "Cache miss for key: user:{}",
    "Retry attempt 2 for upstream",
)


@dataclass
class DemoConfig:
    event_count: int = 50_000
    services: tuple[str, ...] = SERVICES
    seed: int = 20260901
    duration_minutes: int = 240
    start: datetime | None = None
    incident: bool = True


def _new_id(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(16))


def _gen_event(
    rng: random.Random,
    ts: datetime,
    service: str,
    *,
    force_kind: str | None = None,
    trace_pool: dict[str, int] | None = None,
) -> dict:
    is_request = rng.random() < 0.85
    if trace_pool is not None and rng.random() < 0.6:
        trace_id = trace_pool["__current__"]
    else:
        if trace_pool is not None:
            trace_pool["__current__"] = _new_id(rng)
        trace_id = _new_id(rng)
    span_id = _new_id(rng)[:8]
    request_id = trace_id
    path = rng.choice(PATHS) if service != "database" else "/sql/query"
    if force_kind == "error":
        level = "ERROR"
    elif force_kind == "warn":
        level = "WARN"
    elif force_kind == "info":
        level = "INFO"
    else:
        roll = rng.random()
        if roll < 0.78:
            level = "INFO"
        elif roll < 0.95:
            level = "WARN"
        else:
            level = "ERROR"
    status = 200
    duration = rng.uniform(2.0, 120.0)
    if service == "payments":
        duration *= rng.uniform(0.6, 2.0)
    if level == "ERROR":
        status = rng.choice([500, 502, 503, 504])
        duration = rng.uniform(800.0, 4000.0)
    if service == "database":
        status = 200
    msg = ""
    exception_type = None
    if level == "ERROR":
        if service == "payments":
            msg = rng.choice(PAYMENT_ERRORS)
        elif service == "auth":
            msg = rng.choice(AUTH_ERRORS)
        elif service == "orders":
            msg = rng.choice(ORDERS_ERRORS)
        elif service == "database":
            msg = "Database connection lost"
            exception_type = "OperationalError"
        else:
            msg = "Upstream gateway timeout"
            exception_type = "TimeoutError"
    elif level == "WARN":
        msg = rng.choice(GENERIC_WARN).format(rng.randint(100, 9999))
    else:
        if is_request:
            msg = f"{rng.choice(['GET','POST','PUT','DELETE'])} {path} -> {status} in {duration:.1f}ms"
        else:
            msg = f"background job completed in {duration:.1f}ms"
    return {
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": service,
        "logger": LOGGERS[service],
        "host": rng.choice(HOSTS),
        "trace_id": trace_id,
        "span_id": span_id,
        "request_id": request_id,
        "duration_ms": round(duration, 2),
        "status_code": status,
        "exception_type": exception_type,
        "message": msg,
    }


def _emit_jsonl(path: Path, rows: Iterable[dict]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def _emit_text(path: Path, rows: Iterable[dict], service: str) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            ts = row["timestamp"]
            lvl = row["level"]
            svc = row["service"]
            msg = row["message"]
            f.write(f"{ts} {lvl} [{svc}] {msg}\n")
            n += 1
    return n


def generate(target_dir: str | Path, config: DemoConfig | None = None) -> dict[str, str]:
    """Generate a deterministic demo log set under ``target_dir``.

    Returns a dict mapping service name to file path.
    """
    cfg = config or DemoConfig()
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    rng = random.Random(cfg.seed)
    start = cfg.start or datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    total = cfg.event_count
    duration = timedelta(minutes=cfg.duration_minutes)
    n = total
    span = duration.total_seconds() / max(1, n - 1)
    files: dict[str, Path] = {}
    for svc in cfg.services:
        files[svc] = target / f"{svc}.log"
    # Pre-compute which indices belong to each service. We rotate round-robin.
    per_service = n // len(cfg.services)
    # Plan three incidents: payment latency spike, new error signature, error burst.
    incident_spike_start = n // 2
    incident_spike_end = incident_spike_start + per_service // 4
    incident_new_sig_idx = int(n * 0.65)
    incident_burst_start = int(n * 0.85)
    incident_burst_end = min(n, incident_burst_start + per_service // 6)
    new_error_signature = "Payment provider circuit breaker tripped"
    # Find the next index where svc == "payments" at or after incident_new_sig_idx.
    new_sig_target_i = None
    for j in range(incident_new_sig_idx, n):
        if cfg.services[j % len(cfg.services)] == "payments":
            new_sig_target_i = j
            break

    writers = {svc: open(files[svc], "w", encoding="utf-8", newline="") for svc in cfg.services}
    try:
        for i in range(n):
            ts = start + timedelta(seconds=i * span)
            svc = cfg.services[i % len(cfg.services)]
            force_kind: str | None = None
            if cfg.incident and svc == "payments" and incident_spike_start <= i < incident_spike_end:
                # inject latency warning
                writer = writers[svc]
                evt = {
                    "timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "level": "WARN",
                    "service": svc,
                    "logger": LOGGERS[svc],
                    "host": rng.choice(HOSTS),
                    "trace_id": _new_id(rng),
                    "span_id": _new_id(rng)[:8],
                    "request_id": _new_id(rng),
                    "duration_ms": round(rng.uniform(1200.0, 4500.0), 2),
                    "status_code": 200,
                    "exception_type": None,
                    "message": f"Upstream payment provider slow: took {rng.randint(1200,4500)}ms",
                }
                writer.write(json.dumps(evt, ensure_ascii=False) + "\n")
                continue
            if cfg.incident and svc == "payments" and i == new_sig_target_i:
                writer = writers[svc]
                evt = {
                    "timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "level": "ERROR",
                    "service": svc,
                    "logger": LOGGERS[svc],
                    "host": rng.choice(HOSTS),
                    "trace_id": _new_id(rng),
                    "span_id": _new_id(rng)[:8],
                    "request_id": _new_id(rng),
                    "duration_ms": round(rng.uniform(200, 800), 2),
                    "status_code": 503,
                    "exception_type": "CircuitOpenError",
                    "message": new_error_signature,
                }
                writer.write(json.dumps(evt, ensure_ascii=False) + "\n")
                continue
            if cfg.incident and incident_burst_start <= i < incident_burst_end:
                force_kind = "error"
            evt = _gen_event(rng, ts, svc, force_kind=force_kind)
            evt["service"] = svc
            writers[svc].write(json.dumps(evt, ensure_ascii=False) + "\n")
    finally:
        for f in writers.values():
            f.close()
    return {svc: str(files[svc]) for svc in cfg.services}


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="traceforge-demo", description="Generate deterministic demo logs.")
    p.add_argument("target", nargs="?", default="./demo-logs")
    p.add_argument("--events", type=int, default=50_000)
    p.add_argument("--seed", type=int, default=20260901)
    p.add_argument("--duration-minutes", type=int, default=240)
    p.add_argument("--no-incident", action="store_true")
    args = p.parse_args(argv)
    cfg = DemoConfig(
        event_count=args.events,
        seed=args.seed,
        duration_minutes=args.duration_minutes,
        incident=not args.no_incident,
    )
    out = generate(args.target, cfg)
    print(f"Generated {cfg.event_count} events into {len(out)} files under {args.target}")
    for k, v in out.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
