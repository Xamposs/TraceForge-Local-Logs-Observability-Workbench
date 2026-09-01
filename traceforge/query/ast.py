"""TFQL Abstract Syntax Tree nodes.

All AST nodes are plain dataclasses; they are produced by the parser and
consumed by the compiler. No node carries executable code; the compiler
translates them into parameterized DuckDB SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

# --- Values ---


@dataclass(frozen=True)
class Literal:
    value: str | int | float | bool | None
    kind: str  # "string" | "number" | "bool" | "null"


# --- Expressions ---


@dataclass(frozen=True)
class FieldRef:
    name: str  # canonical name, e.g. "severity"


@dataclass(frozen=True)
class Comparison:
    field: FieldRef
    op: str  # "=" | "!=" | ">" | ">=" | "<" | "<="
    value: Literal


@dataclass(frozen=True)
class ContainsExpression:
    field: FieldRef
    op: str  # "CONTAINS" | "STARTS_WITH" | "ENDS_WITH"
    value: Literal
    case_sensitive: bool = False


@dataclass(frozen=True)
class InExpression:
    field: FieldRef
    values: tuple[Literal, ...]


@dataclass(frozen=True)
class NotExpression:
    expr: Expression


@dataclass(frozen=True)
class AndExpression:
    left: Expression
    right: Expression


@dataclass(frozen=True)
class OrExpression:
    left: Expression
    right: Expression


Expression = Union[
    Comparison,
    ContainsExpression,
    InExpression,
    NotExpression,
    AndExpression,
    OrExpression,
    "BooleanLiteral",
]


@dataclass(frozen=True)
class BooleanLiteral:
    value: bool


# --- Pipeline stages ---


@dataclass(frozen=True)
class SortStage:
    field: str  # canonical or alias
    descending: bool = False


@dataclass(frozen=True)
class LimitStage:
    count: int


@dataclass(frozen=True)
class AggregateCall:
    function: str  # count | avg | min | max | p95 | p50 | sum
    field: str | None = None
    alias: str | None = None


@dataclass(frozen=True)
class StatsStage:
    aggregates: tuple[AggregateCall, ...]
    group_by: tuple[str, ...] = ()


# --- Program ---


@dataclass(frozen=True)
class Query:
    expression: Expression | None = None
    sort: list[SortStage] = field(default_factory=list)
    limit: int | None = None
    stats: StatsStage | None = None
