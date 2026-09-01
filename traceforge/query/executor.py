"""TFQL executor.

Takes a query string, parses + compiles it, and runs it against a
:class:`Database` instance. Returns a structured result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from traceforge.query.compiler import (
    MAX_LIMIT,
    CompilationError,
    CompiledQuery,
    compile_query,
)
from traceforge.query.grammar import TFQLSyntaxError
from traceforge.query.parser import parse as parse_query
from traceforge.storage.database import Database


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    is_aggregation: bool
    row_count: int
    elapsed_ms: float
    sql: str
    truncated: bool = False


class QueryError(Exception):
    """User-visible TFQL error (syntax or semantic)."""


def _wrap(exc: Exception) -> QueryError:
    if isinstance(exc, (TFQLSyntaxError, CompilationError)):
        return QueryError(str(exc))
    if isinstance(exc, QueryError):
        return exc
    return QueryError(f"Query failed: {exc}")


def execute(
    db: Database,
    source: str,
    *,
    base_limit: int = 1000,
    max_limit: int = MAX_LIMIT,
) -> QueryResult:
    try:
        ast = parse_query(source)
        compiled = compile_query(ast, base_limit=base_limit, max_limit=max_limit)
    except (TFQLSyntaxError, CompilationError) as e:
        raise _wrap(e) from e
    return _run(db, compiled)


def execute_ast(db: Database, ast, *, base_limit: int = 1000, max_limit: int = MAX_LIMIT) -> QueryResult:
    try:
        compiled = compile_query(ast, base_limit=base_limit, max_limit=max_limit)
    except CompilationError as e:
        raise _wrap(e) from e
    return _run(db, compiled)


def _run(db: Database, compiled: CompiledQuery) -> QueryResult:
    t0 = time.perf_counter()
    rel = db.execute(compiled.sql, compiled.params)
    rows = list(rel.fetchall())
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    cols = [d[0] for d in rel.description] if rel.description else list(compiled.select_columns)
    return QueryResult(
        columns=cols,
        rows=rows,
        is_aggregation=compiled.is_aggregation,
        row_count=len(rows),
        elapsed_ms=elapsed_ms,
        sql=compiled.sql,
        truncated=compiled.is_aggregation is False and len(rows) >= MAX_LIMIT,
    )


def validate(source: str) -> tuple[bool, str]:
    try:
        ast = parse_query(source)
        compile_query(ast)
    except (TFQLSyntaxError, CompilationError) as e:
        return False, str(e)
    return True, "ok"
