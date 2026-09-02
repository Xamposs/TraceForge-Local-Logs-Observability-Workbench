"""TFQL aggregate sort by alias (Section 20)."""

from __future__ import annotations

import pytest

from traceforge.query import CompilationError, parse
from traceforge.query.compiler import compile_query


def test_sort_by_aggregate_alias() -> None:
    q = parse("| stats count() AS n by service | sort n desc")
    c = compile_query(q)
    assert "ORDER BY" in c.sql
    # The alias "n" must be referenced (quoted) in the ORDER BY.
    assert '"n"' in c.sql
    assert "desc" in c.sql.lower()


def test_sort_by_default_aggregate_name() -> None:
    """If no AS alias is given, the default name (function+field) is
    accepted in sort."""
    q = parse("| stats count(service) by service | sort count_service asc")
    c = compile_query(q)
    assert '"count_service"' in c.sql
    assert "asc" in c.sql.lower()


def test_sort_by_unknown_field_in_stats_still_rejected() -> None:
    """Unknown field that is neither a real column nor an aggregate
    alias is rejected with a clear error."""
    q = parse("| stats count() by service | sort not_a_field asc")
    with pytest.raises(CompilationError):
        compile_query(q)
