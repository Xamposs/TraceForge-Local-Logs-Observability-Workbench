"""TFQL tests — parser, AST, compiler, executor, safety against SQL injection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from traceforge.models.events import LogEvent, SourceFingerprint, SourceStats
from traceforge.models.sources import SourceConfig
from traceforge.query import (
    CompilationError,
    QueryError,
    TFQLSyntaxError,
    compile_query,
    execute,
    parse,
    tokenize,
    validate,
)
from traceforge.storage import Database, EventRepository


@pytest.fixture()
def populated_db(database: Database) -> Database:
    repo = EventRepository(database)
    sid = repo.next_source_id()
    cfg = SourceConfig(path="/tmp/x", alias="x")
    fp = SourceFingerprint(path="/tmp/x", size=0, mtime_ns=0, sample_hash="abc", content_kind="text")
    stats = SourceStats(path="/tmp/x", parser="text")
    repo.upsert_source(sid, cfg, fp, "text", stats)
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    rows = []
    for i in range(1, 11):
        ev = LogEvent(
            event_id=f"e{i}",
            source="x",
            source_path="/tmp/x",
            line_number=i,
            timestamp=now,
            ingested_at=now,
            severity=("ERROR" if i % 2 == 0 else "INFO"),
            message=f"msg {i}",
            raw_text="...",
            service=("api" if i % 3 == 0 else "payments"),
            trace_id=("t1" if i <= 5 else "t2"),
            request_id=f"r{i}",
            duration_ms=float(i * 10),
            status_code=(200 if i % 2 == 0 else 500),
            raw_format="text",
        )
        rows.append(ev)
    repo.insert_events(rows, sid)
    return database


# --- Lexer / Parser ---


def test_lexer_basic() -> None:
    tokens = tokenize('level = ERROR AND message CONTAINS "x"')
    kinds = [t.kind.name for t in tokens]
    assert "IDENT" in kinds
    assert "STRING" in kinds
    assert "AND" in [t.upper for t in tokens if t.kind.name == "IDENT"]


def test_lexer_string_escapes() -> None:
    tokens = tokenize(r'message CONTAINS "a\"b"')
    assert tokens[0].text == 'a"b' or any(t.text == 'a"b' for t in tokens)


def test_parse_simple_equality() -> None:
    q = parse("level = ERROR")
    from traceforge.query.ast import Comparison

    assert isinstance(q.expression, Comparison)


def test_parse_and_or_not_precedence() -> None:
    q = parse("level = ERROR AND NOT (service = api OR service = payments)")
    from traceforge.query.ast import AndExpression, NotExpression, OrExpression

    assert isinstance(q.expression, AndExpression)
    assert isinstance(q.expression.right, NotExpression)
    assert isinstance(q.expression.right.expr, OrExpression)


def test_parse_contains() -> None:
    q = parse('message CONTAINS "timeout"')
    from traceforge.query.ast import ContainsExpression

    assert isinstance(q.expression, ContainsExpression)


def test_parse_in() -> None:
    q = parse('service IN ("api", "payments")')
    from traceforge.query.ast import InExpression

    assert isinstance(q.expression, InExpression)
    assert len(q.expression.values) == 2


def test_parse_starts_with_ends_with() -> None:
    q = parse('message STARTS_WITH "ERR"')
    assert q.expression.op == "STARTS_WITH"
    q = parse('message ENDS_WITH "tail"')
    assert q.expression.op == "ENDS_WITH"


def test_parse_pipeline_stages() -> None:
    q = parse("level = ERROR | sort timestamp desc | limit 50")
    assert q.sort and q.sort[0].field == "timestamp"
    assert q.sort[0].descending
    assert q.limit == 50


def test_parse_stats_with_group_by() -> None:
    q = parse("| stats count(), avg(duration_ms) as avg_ms by service")
    assert q.stats is not None
    assert len(q.stats.aggregates) == 2
    assert q.stats.group_by == ("service",)


def test_parse_stats_only() -> None:
    q = parse("| stats count()")
    assert q.stats is not None


def test_syntax_error_unexpected_token() -> None:
    with pytest.raises(TFQLSyntaxError):
        parse("level =")


def test_syntax_error_unknown_function() -> None:
    with pytest.raises(TFQLSyntaxError):
        parse("| stats p999(duration_ms)")


def test_syntax_error_missing_close_paren() -> None:
    with pytest.raises(TFQLSyntaxError):
        parse("level = ERROR AND (service = api")


def test_validation() -> None:
    ok, msg = validate("level = ERROR")
    assert ok
    ok, msg = validate("servce = ERROR")
    assert not ok and "Unknown field" in msg


def test_suggestion_in_error() -> None:
    ok, msg = validate("sevrice = api")
    assert not ok
    assert "Did you mean" in msg


# --- Compiler / SQL ---


def test_compile_simple_equality_is_parameterized() -> None:
    q = parse("level = ERROR")
    c = compile_query(q)
    assert "?" in c.sql
    assert c.params == ["ERROR"]
    assert "ERROR" not in c.sql.replace('"severity"', "")  # value not inlined


def test_compile_in_list() -> None:
    q = parse('service IN ("api", "payments")')
    c = compile_query(q)
    assert c.sql.count("?") == 2
    assert c.params == ["api", "payments"]


def test_compile_contains() -> None:
    q = parse('message CONTAINS "timeout"')
    c = compile_query(q)
    assert "POSITION" in c.sql
    assert c.params == ["timeout"]


def test_compile_stats() -> None:
    q = parse("| stats count(), avg(duration_ms) by service")
    c = compile_query(q)
    assert "GROUP BY" in c.sql
    assert "AVG" in c.sql or "avg" in c.sql.lower()
    assert c.is_aggregation


def test_compile_unknown_field() -> None:
    q = parse("servce = api")
    with pytest.raises(CompilationError):
        compile_query(q)


def test_compile_alias_resolution() -> None:
    q = parse("level = ERROR")
    c = compile_query(q)
    assert '"severity"' in c.sql
    q = parse("traceId = abc")
    c = compile_query(q)
    assert '"trace_id"' in c.sql


# --- SQL injection safety ---


@pytest.mark.parametrize(
    "malicious",
    [
        "level = ERROR; DROP TABLE events; --",
        "level = ERROR; SELECT * FROM secrets; --",
        'service = "x"; DELETE FROM events WHERE 1=1; --',
    ],
)
def test_malicious_values_become_parameters(malicious: str) -> None:
    """TFQL must never splice values into SQL syntax.

    The injection strings attempt to break out of the comparison and run
    extra SQL. The lexer treats ``--`` as a comment and ``;`` is not a
    valid token, so the only safe way to handle the input is to either
    raise a clean syntax error or to bind every value as a parameter.
    Either way, the resulting SQL must not contain ``DROP``, ``DELETE``,
    etc., and must not contain user-controlled string fragments outside
    of parameter placeholders.
    """
    try:
        q = parse(malicious)
        c = compile_query(q)
    except (TFQLSyntaxError, CompilationError):
        return  # Acceptable: clean syntax error is also safe.
    assert c.sql.count("?") >= 1
    assert "DROP" not in c.sql.upper()
    assert "DELETE" not in c.sql.upper()
    assert ";" not in c.sql
    for param in c.params:
        assert isinstance(param, (str, int, float, bool, type(None)))


def test_execute_malicious_does_not_drop_table(populated_db: Database) -> None:
    """Run a malicious TFQL and verify the table is intact afterwards."""
    before = populated_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(QueryError):
        execute(populated_db, "level = ERROR; DROP TABLE events; --")
    after = populated_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after


def test_execute_malicious_unicode_does_not_drop_table(populated_db: Database) -> None:
    """Malicious query should run safely (either error or empty result)
    and never touch the events table."""
    before = populated_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    try:
        result = execute(populated_db, 'service = "x\u0027; DROP TABLE events; --"')
        # If it parses, it should be safely parameterized and return 0 rows.
        assert result.row_count == 0
    except QueryError:
        pass  # A clean error is also acceptable.
    after = populated_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after


# --- Executor / integration ---


def test_execute_simple(populated_db: Database) -> None:
    r = execute(populated_db, "level = ERROR")
    assert r.row_count == 5
    assert r.elapsed_ms >= 0
    assert "event_id" in r.columns


def test_execute_and_or_not(populated_db: Database) -> None:
    r = execute(populated_db, "level = ERROR AND service = api")
    assert r.row_count >= 1
    r = execute(populated_db, "NOT level = ERROR")
    assert r.row_count == 5
    r = execute(populated_db, 'level = ERROR OR level = "INFO"')
    assert r.row_count == 10


def test_execute_in(populated_db: Database) -> None:
    r = execute(populated_db, 'service IN ("api", "payments")')
    assert r.row_count == 10


def test_execute_sort_limit(populated_db: Database) -> None:
    r = execute(populated_db, "| sort duration_ms desc | limit 3")
    assert r.row_count == 3
    durations = [row[r.columns.index("duration_ms")] for row in r.rows]
    assert durations == sorted(durations, reverse=True)


def test_execute_stats_by(populated_db: Database) -> None:
    r = execute(populated_db, "| stats count() by service")
    assert r.is_aggregation
    assert r.row_count == 2  # api and payments


def test_execute_query_error_does_not_raise(populated_db: Database) -> None:
    with pytest.raises(QueryError):
        execute(populated_db, "level = ERROR AND (")


def test_execute_unicode_value(populated_db: Database) -> None:
    """Insert a unicode message and query for it."""
    repo = EventRepository(populated_db)
    sid = populated_db.execute("SELECT id FROM sources LIMIT 1").fetchone()[0]
    ev = LogEvent(
        event_id="unicode-1",
        source="x",
        source_path="/tmp/x",
        line_number=99,
        timestamp=datetime.now(tz=UTC),
        ingested_at=datetime.now(tz=UTC),
        severity="INFO",
        message="héllo world",
        raw_text="...",
        raw_format="text",
    )
    repo.insert_events([ev], sid)
    r = execute(populated_db, 'message CONTAINS "héllo"')
    assert r.row_count == 1


def test_validate_ok() -> None:
    ok, msg = validate("level = ERROR AND duration_ms > 500")
    assert ok, msg


def test_validate_unknown_field() -> None:
    ok, msg = validate("servce = api")
    assert not ok
