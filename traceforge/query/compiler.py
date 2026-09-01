"""TFQL -> parameterized DuckDB SQL compiler.

This is the safety boundary. The compiler:

* validates every identifier against :data:`EVENT_FIELDS` /
  :data:`EVENT_FIELD_ALIASES` (whitelisted column set),
* translates comparison/contains/in expressions into parameter placeholders,
* never concatenates user-controlled text into SQL syntax,
* caps LIMIT to a safe maximum,
* maps TFQL aggregate names to DuckDB functions (percentile_cont),
* uses parameter binding for all values.

SQL injection from log content or query input is therefore structurally
impossible: a malicious value becomes a parameter, not a piece of SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from traceforge.query.ast import (
    AggregateCall,
    AndExpression,
    BooleanLiteral,
    Comparison,
    ContainsExpression,
    Expression,
    InExpression,
    Literal,
    NotExpression,
    OrExpression,
    Query,
    SortStage,
)
from traceforge.storage.repository import (
    EVENT_FIELDS,
    resolve_event_field,
)

MAX_LIMIT = 100_000


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any] = field(default_factory=list)
    select_columns: list[str] = field(default_factory=list)
    is_aggregation: bool = False


class CompilationError(Exception):
    """Raised for semantic problems (unknown fields, bad aggregates, ...)."""


def _canonical_field(name: str) -> str:
    canon = resolve_event_field(name)
    if canon is None:
        suggestions = _suggest(name, EVENT_FIELDS)
        msg = f"Unknown field: {name}"
        if suggestions:
            msg += f"\nDid you mean: {', '.join(suggestions[:3])}?"
        raise CompilationError(msg)
    return canon


def _suggest(name: str, candidates: Iterable[str]) -> list[str]:
    name_l = name.lower()
    scored: list[tuple[int, str]] = []
    for c in candidates:
        c_l = c.lower()
        score = 0
        if c_l.startswith(name_l) or name_l.startswith(c_l):
            score = 0
        elif name_l in c_l or c_l in name_l:
            score = 1
        else:
            # edit distance via difflib (stdlib, no extra dep)
            import difflib

            ratio = difflib.SequenceMatcher(None, name_l, c_l).ratio()
            if ratio >= 0.6:
                # Lower score = better match.
                score = int((1.0 - ratio) * 100)
            else:
                continue
        scored.append((score, c))
    scored.sort()
    return [c for _, c in scored]


def _literal_to_param(lit: Literal) -> Any:
    if lit.kind == "string":
        return lit.value
    if lit.kind == "number":
        return lit.value
    if lit.kind == "bool":
        return lit.value
    return None


def _field_placeholder(field_name: str) -> str:
    return f'"{field_name}"'


def _compile_expression(expr: Expression) -> tuple[str, list[Any]]:
    if isinstance(expr, BooleanLiteral):
        return ("TRUE" if expr.value else "FALSE"), []
    if isinstance(expr, Comparison):
        col = _canonical_field(expr.field.name)
        op = expr.op
        param = _literal_to_param(expr.value)
        if expr.value.kind in ("string", "number", "bool", "null"):
            sql = f"{_field_placeholder(col)} {op} ?"
            return sql, [param]
        raise CompilationError(f"Unsupported literal kind: {expr.value.kind}")
    if isinstance(expr, ContainsExpression):
        col = _canonical_field(expr.field.name)
        v = _literal_to_param(expr.value)
        if expr.op == "CONTAINS":
            return f"POSITION(? IN {_field_placeholder(col)}) > 0", [v]
        if expr.op == "STARTS_WITH":
            return f"{_field_placeholder(col)} LIKE ? || '%'", [v]
        if expr.op == "ENDS_WITH":
            return f"{_field_placeholder(col)} LIKE '%' || ?", [v]
        raise CompilationError(f"Unknown op: {expr.op}")
    if isinstance(expr, InExpression):
        col = _canonical_field(expr.field.name)
        if not expr.values:
            return "FALSE", []
        placeholders = ",".join("?" for _ in expr.values)
        params = [_literal_to_param(v) for v in expr.values]
        return f"{_field_placeholder(col)} IN ({placeholders})", params
    if isinstance(expr, NotExpression):
        sql, params = _compile_expression(expr.expr)
        return f"NOT ({sql})", params
    if isinstance(expr, AndExpression):
        l_sql, l_params = _compile_expression(expr.left)
        r_sql, r_params = _compile_expression(expr.right)
        return f"({l_sql}) AND ({r_sql})", l_params + r_params
    if isinstance(expr, OrExpression):
        l_sql, l_params = _compile_expression(expr.left)
        r_sql, r_params = _compile_expression(expr.right)
        return f"({l_sql}) OR ({r_sql})", l_params + r_params
    raise CompilationError(f"Unsupported expression: {expr!r}")


def _compile_sort(stage: SortStage) -> str:
    col = _canonical_field(stage.field)
    direction = "DESC" if stage.descending else "ASC"
    # Timestamp and string sorts are supported natively.
    return f"{_field_placeholder(col)} {direction} NULLS LAST"


def _compile_agg(agg: AggregateCall) -> tuple[str, str]:
    fn = agg.function
    alias = agg.alias
    if fn == "count":
        if agg.field is None:
            return ("COUNT(*)", alias or "count")
        col = _canonical_field(agg.field)
        return (f"COUNT({_field_placeholder(col)})", alias or f"count_{col}")
    if fn == "avg":
        col = _canonical_field(agg.field or "")
        return (f"AVG({_field_placeholder(col)})", alias or f"avg_{col}")
    if fn == "min":
        col = _canonical_field(agg.field or "")
        return (f"MIN({_field_placeholder(col)})", alias or f"min_{col}")
    if fn == "max":
        col = _canonical_field(agg.field or "")
        return (f"MAX({_field_placeholder(col)})", alias or f"max_{col}")
    if fn == "sum":
        col = _canonical_field(agg.field or "")
        return (f"SUM({_field_placeholder(col)})", alias or f"sum_{col}")
    if fn == "p50":
        col = _canonical_field(agg.field or "")
        return (f"quantile_cont({_field_placeholder(col)}, 0.50)", alias or f"p50_{col}")
    if fn == "p95":
        col = _canonical_field(agg.field or "")
        return (f"quantile_cont({_field_placeholder(col)}, 0.95)", alias or f"p95_{col}")
    raise CompilationError(f"Unknown aggregate function: {fn}")


def compile_query(query: Query, *, base_limit: int = 1000, max_limit: int = MAX_LIMIT) -> CompiledQuery:
    """Compile a parsed TFQL Query to parameterized SQL.

    The output always includes a stable set of result columns, regardless of
    pipeline stage, so the UI can render rows consistently.
    """
    where_sql = "TRUE"
    params: list[Any] = []
    if query.expression is not None:
        where_sql, params = _compile_expression(query.expression)

    # Aggregation path.
    if query.stats is not None:
        agg_cols: list[str] = []
        for a in query.stats.aggregates:
            sql_expr, alias = _compile_agg(a)
            agg_cols.append(f'{sql_expr} AS "{alias}"')
        group_cols: list[str] = []
        for g in query.stats.group_by:
            canon = _canonical_field(g)
            group_cols.append(_field_placeholder(canon))
        select = group_cols + agg_cols if group_cols else agg_cols
        if not select:
            raise CompilationError("STATS requires at least one aggregate")
        group_by = f"GROUP BY {', '.join(group_cols)}" if group_cols else ""
        order = ""
        if query.sort:
            order = "ORDER BY " + ", ".join(_compile_sort(s) for s in query.sort)
        limit = ""
        if query.limit is not None:
            limit = f"LIMIT {int(min(max_limit, max(0, query.limit)))}"
        sql = (
            f"SELECT {', '.join(select)} FROM events " f"WHERE {where_sql} {group_by} {order} {limit}"
        ).strip()
        return CompiledQuery(sql=sql, params=params, is_aggregation=True, select_columns=select)

    # Row path.
    select = [
        "event_id",
        "timestamp",
        "ingested_at",
        "severity",
        "service",
        "logger",
        "host",
        "message",
        "raw_text",
        "source_path",
        "source_alias",
        "line_number",
        "trace_id",
        "span_id",
        "parent_span_id",
        "request_id",
        "session_id",
        "duration_ms",
        "status_code",
        "exception_type",
        "raw_format",
    ]
    select_clause = ", ".join(_field_placeholder(c) for c in select)
    order = ""
    if query.sort:
        order = "ORDER BY " + ", ".join(_compile_sort(s) for s in query.sort)
    else:
        order = 'ORDER BY "timestamp" ASC NULLS LAST, "line_number" ASC'
    eff_limit = query.limit if query.limit is not None else base_limit
    eff_limit = int(min(max_limit, max(1, eff_limit)))
    sql = (f"SELECT {select_clause} FROM events " f"WHERE {where_sql} {order} LIMIT {eff_limit}").strip()
    return CompiledQuery(sql=sql, params=params, is_aggregation=False, select_columns=select)
