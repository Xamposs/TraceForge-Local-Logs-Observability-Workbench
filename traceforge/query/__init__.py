"""TFQL — TraceForge Query Language.

A small, deterministic, safe query language for the local event store.
"""

from traceforge.query.ast import (
    AggregateCall,
    AndExpression,
    BooleanLiteral,
    Comparison,
    ContainsExpression,
    Expression,
    FieldRef,
    InExpression,
    LimitStage,
    Literal,
    NotExpression,
    OrExpression,
    Query,
    SortStage,
    StatsStage,
)
from traceforge.query.compiler import (
    MAX_LIMIT,
    CompilationError,
    CompiledQuery,
    compile_query,
)
from traceforge.query.executor import QueryError, QueryResult, execute, validate
from traceforge.query.grammar import TFQLSyntaxError, Token, tokenize
from traceforge.query.parser import parse

__all__ = [
    "AggregateCall",
    "AndExpression",
    "BooleanLiteral",
    "Comparison",
    "CompilationError",
    "CompiledQuery",
    "ContainsExpression",
    "Expression",
    "FieldRef",
    "InExpression",
    "LimitStage",
    "Literal",
    "MAX_LIMIT",
    "NotExpression",
    "OrExpression",
    "Query",
    "QueryError",
    "QueryResult",
    "SortStage",
    "StatsStage",
    "TFQLSyntaxError",
    "Token",
    "compile_query",
    "execute",
    "parse",
    "tokenize",
    "validate",
]
