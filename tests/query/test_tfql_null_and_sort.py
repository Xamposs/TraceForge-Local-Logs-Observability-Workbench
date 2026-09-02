"""TFQL NULL semantics and multi-sort regression tests (Sections 18-19).

* 18 — ``field = NULL`` compiles to ``field IS NULL``; ``field != NULL``
      to ``field IS NOT NULL``; ``field > NULL`` is rejected.
* 19 — multiple ``sort`` stages are preserved (not silently collapsed).
"""

from __future__ import annotations

import pytest

from traceforge.query import CompilationError, parse


def test_equals_null_compiles_to_is_null() -> None:
    q = parse("status_code = NULL")
    from traceforge.query.compiler import compile_query

    c = compile_query(q)
    assert "IS NULL" in c.sql
    assert "= ?" not in c.sql


def test_not_equals_null_compiles_to_is_not_null() -> None:
    q = parse("status_code != NULL")
    from traceforge.query.compiler import compile_query

    c = compile_query(q)
    assert "IS NOT NULL" in c.sql
    assert "!= ?" not in c.sql


@pytest.mark.parametrize(
    "query",
    [
        "duration_ms > NULL",
        "duration_ms >= NULL",
        "duration_ms < NULL",
        "duration_ms <= NULL",
    ],
)
def test_ordered_compare_with_null_is_rejected(query: str) -> None:
    from traceforge.query.compiler import compile_query

    q = parse(query)
    with pytest.raises(CompilationError):
        compile_query(q)


def test_multi_sort_preserves_all_fields() -> None:
    q = parse("| sort timestamp desc, line_number asc")
    assert q.sort is not None
    assert len(q.sort) == 2
    assert q.sort[0].field == "timestamp"
    assert q.sort[0].descending is True
    assert q.sort[1].field == "line_number"
    assert q.sort[1].descending is False


def test_multi_sort_compiles_in_order() -> None:
    from traceforge.query.compiler import compile_query

    q = parse("| sort timestamp desc, severity asc")
    c = compile_query(q)
    # The ORDER BY clause must contain both columns, in the same order.
    order_idx = c.sql.find("ORDER BY")
    assert order_idx != -1
    tail = c.sql[order_idx:]
    ts_idx = tail.find("timestamp")
    sev_idx = tail.find("severity")
    assert ts_idx != -1 and sev_idx != -1
    assert ts_idx < sev_idx, c.sql
