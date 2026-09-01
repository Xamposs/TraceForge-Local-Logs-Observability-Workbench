"""TFQL parser — recursive-descent over the token stream.

Grammar (v0.1):

    program     := expression ( PIPE stage )*
    stage       := sort_stage | limit_stage | stats_stage
    sort_stage  := SORT IDENT ( ASC | DESC )? ( COMMA IDENT ( ASC | DESC )? )*
    limit_stage := LIMIT NUMBER
    stats_stage := STATS agg_list ( BY IDENT ( COMMA IDENT )* )?
    agg_list    := agg ( COMMA agg )*
    agg         := agg_fn ( LPAREN IDENT RPAREN )? ( AS IDENT )?
    agg_fn      := count | avg | min | max | sum | p50 | p95

    expression  := or_expr
    or_expr     := and_expr ( OR and_expr )*
    and_expr    := not_expr ( AND not_expr )*
    not_expr    := NOT not_expr | atom
    atom        := LPAREN expression RPAREN
                 | IDENT op value
                 | IDENT IN LPAREN value ( COMMA value )* RPAREN
                 | IDENT CONTAINS STRING
                 | IDENT STARTS_WITH STRING
                 | IDENT ENDS_WITH STRING
                 | TRUE | FALSE | NULL
    op          := = | != | > | >= | < | <=
    value       := STRING | NUMBER | TRUE | FALSE | NULL

Tokens are produced by :mod:`traceforge.query.grammar`. Identifier semantics
are validated later by the compiler.
"""

from __future__ import annotations

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
from traceforge.query.grammar import WORD_OPS, TFQLSyntaxError, TKind, Token

AGG_FUNCTIONS = {"count", "avg", "min", "max", "sum", "p50", "p95"}


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._i = 0

    # --- helpers ---

    def _peek(self, offset: int = 0) -> Token:
        if self._i + offset >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[self._i + offset]

    def _advance(self) -> Token:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def _expect(self, kind: TKind, text: str | None = None) -> Token:
        tok = self._peek()
        if tok.kind is not kind or (text is not None and tok.upper != text.upper()):
            raise TFQLSyntaxError(self._expected_message(kind, text), tok.pos, max(1, len(tok.text)))
        return self._advance()

    def _expected_message(self, kind: TKind, text: str | None) -> str:
        if text is not None:
            return f"Expected {text}, got {self._peek().text!r}"
        return f"Expected {kind.name}, got {self._peek().text!r}"

    def _accept(self, kind: TKind, text: str | None = None) -> Token | None:
        tok = self._peek()
        if tok.kind is kind and (text is None or tok.upper == text.upper()):
            return self._advance()
        return None

    def _is_keyword(self, text: str) -> bool:
        tok = self._peek()
        return tok.kind is TKind.IDENT and tok.upper == text

    # --- public ---

    def parse(self) -> Query:
        if self._peek().kind is TKind.EOF:
            return Query()
        expr: Expression | None = None
        if self._peek().kind is not TKind.PIPE:
            expr = self._parse_expression()
        sort: list[SortStage] = []
        limit: int | None = None
        stats: StatsStage | None = None
        if self._peek().kind is TKind.PIPE:
            self._advance()
            stage = self._parse_stage()
            if isinstance(stage, SortStage):
                sort.append(stage)
            elif isinstance(stage, LimitStage):
                limit = stage.count
            elif isinstance(stage, StatsStage):
                stats = stage
            else:  # pragma: no cover
                raise TFQLSyntaxError(f"Unknown stage: {stage!r}", self._peek().pos)
        while self._accept(TKind.PIPE) is not None:
            stage = self._parse_stage()
            if isinstance(stage, SortStage):
                sort.append(stage)
            elif isinstance(stage, LimitStage):
                limit = stage.count
            elif isinstance(stage, StatsStage):
                stats = stage
            else:  # pragma: no cover
                raise TFQLSyntaxError(f"Unknown stage: {stage!r}", self._peek().pos)
        self._expect(TKind.EOF)
        return Query(expression=expr, sort=sort, limit=limit, stats=stats)

    # --- stages ---

    def _parse_stage(self):
        if self._is_keyword("SORT"):
            return self._parse_sort()
        if self._is_keyword("LIMIT"):
            return self._parse_limit()
        if self._is_keyword("STATS"):
            return self._parse_stats()
        raise TFQLSyntaxError(f"Expected pipeline stage, got {self._peek().text!r}", self._peek().pos)

    def _parse_sort(self) -> SortStage:
        self._advance()  # SORT
        field_tok = self._expect(TKind.IDENT)
        desc = False
        if self._is_keyword("ASC"):
            self._advance()
        elif self._is_keyword("DESC"):
            self._advance()
            desc = True
        # Consume additional comma-separated fields; return only the first for
        # v0.1 to keep the contract simple, but accept and ignore extras.
        while self._accept(TKind.COMMA) is not None:
            self._expect(TKind.IDENT)
            if self._is_keyword("ASC") or self._is_keyword("DESC"):
                self._advance()
        return SortStage(field=field_tok.text, descending=desc)

    def _parse_limit(self) -> LimitStage:
        self._advance()  # LIMIT
        num_tok = self._expect(TKind.NUMBER)
        try:
            value = int(num_tok.text)
        except ValueError as e:
            raise TFQLSyntaxError(f"Invalid LIMIT value: {num_tok.text!r}", num_tok.pos) from e
        if value < 0:
            raise TFQLSyntaxError("LIMIT must be non-negative", num_tok.pos)
        return LimitStage(count=value)

    def _parse_stats(self) -> StatsStage:
        self._advance()  # STATS
        aggs: list[AggregateCall] = [self._parse_agg()]
        while self._accept(TKind.COMMA) is not None:
            aggs.append(self._parse_agg())
        group_by: list[str] = []
        if self._is_keyword("BY"):
            self._advance()
            tok = self._expect(TKind.IDENT)
            group_by.append(tok.text)
            while self._accept(TKind.COMMA) is not None:
                tok = self._expect(TKind.IDENT)
                group_by.append(tok.text)
        return StatsStage(aggregates=tuple(aggs), group_by=tuple(group_by))

    def _parse_agg(self) -> AggregateCall:
        tok = self._expect(TKind.IDENT)
        fn = tok.upper.lower()
        if fn not in AGG_FUNCTIONS:
            raise TFQLSyntaxError(f"Unknown function: {fn}", tok.pos, len(tok.text))
        field: str | None = None
        if self._accept(TKind.LPAREN) is not None:
            # count() is the only zero-arg form; everything else needs an ident.
            if self._peek().kind is TKind.RPAREN:
                if fn != "count":
                    raise TFQLSyntaxError(f"Function {fn} requires an argument", tok.pos)
                self._advance()
            else:
                inner = self._expect(TKind.IDENT)
                self._expect(TKind.RPAREN)
                field = inner.text
        else:
            if fn != "count":
                raise TFQLSyntaxError(f"Function {fn} requires an argument", tok.pos)
        alias: str | None = None
        if self._is_keyword("AS"):
            self._advance()
            alias_tok = self._expect(TKind.IDENT)
            alias = alias_tok.text
        return AggregateCall(function=fn, field=field, alias=alias)

    # --- expressions ---

    def _parse_expression(self) -> Expression:
        return self._parse_or()

    def _parse_or(self) -> Expression:
        left = self._parse_and()
        while self._is_keyword("OR"):
            self._advance()
            right = self._parse_and()
            left = OrExpression(left=left, right=right)
        return left

    def _parse_and(self) -> Expression:
        left = self._parse_not()
        while self._is_keyword("AND"):
            self._advance()
            right = self._parse_not()
            left = AndExpression(left=left, right=right)
        return left

    def _parse_not(self) -> Expression:
        if self._is_keyword("NOT"):
            self._advance()
            return NotExpression(expr=self._parse_not())
        return self._parse_atom()

    def _parse_atom(self) -> Expression:
        if self._accept(TKind.LPAREN) is not None:
            inner = self._parse_expression()
            self._expect(TKind.RPAREN)
            return inner
        # booleans / null
        if self._is_keyword("TRUE"):
            self._advance()
            return BooleanLiteral(value=True)
        if self._is_keyword("FALSE"):
            self._advance()
            return BooleanLiteral(value=False)
        if self._is_keyword("NULL"):
            self._advance()
            return Literal(value=None, kind="null")
        # Field reference: IDENT followed by operator
        field_tok = self._expect(TKind.IDENT)
        if field_tok.upper in WORD_OPS:
            raise TFQLSyntaxError(f"Unexpected keyword {field_tok.text!r} in expression", field_tok.pos)
        field = FieldRef(name=field_tok.text)
        # IN
        if self._is_keyword("IN"):
            self._advance()
            self._expect(TKind.LPAREN)
            values: list[Literal] = [self._parse_value()]
            while self._accept(TKind.COMMA) is not None:
                values.append(self._parse_value())
            self._expect(TKind.RPAREN)
            return InExpression(field=field, values=tuple(values))
        # Word operators
        if self._is_keyword("CONTAINS"):
            self._advance()
            v = self._parse_value()
            if v.kind != "string":
                raise TFQLSyntaxError("CONTAINS requires a string literal", field_tok.pos)
            return ContainsExpression(field=field, op="CONTAINS", value=v)
        if self._is_keyword("STARTS_WITH"):
            self._advance()
            v = self._parse_value()
            if v.kind != "string":
                raise TFQLSyntaxError("STARTS_WITH requires a string literal", field_tok.pos)
            return ContainsExpression(field=field, op="STARTS_WITH", value=v)
        if self._is_keyword("ENDS_WITH"):
            self._advance()
            v = self._parse_value()
            if v.kind != "string":
                raise TFQLSyntaxError("ENDS_WITH requires a string literal", field_tok.pos)
            return ContainsExpression(field=field, op="ENDS_WITH", value=v)
        # Comparison
        op = self._parse_comparison_op()
        v = self._parse_value()
        return Comparison(field=field, op=op, value=v)

    def _parse_comparison_op(self) -> str:
        tok = self._peek()
        if tok.kind is TKind.EQ:
            self._advance()
            return "="
        if tok.kind is TKind.NE:
            self._advance()
            return "!="
        if tok.kind is TKind.GT:
            self._advance()
            return ">"
        if tok.kind is TKind.GE:
            self._advance()
            return ">="
        if tok.kind is TKind.LT:
            self._advance()
            return "<"
        if tok.kind is TKind.LE:
            self._advance()
            return "<="
        raise TFQLSyntaxError(f"Expected comparison operator, got {tok.text!r}", tok.pos)

    def _parse_value(self) -> Literal:
        tok = self._peek()
        if tok.kind is TKind.STRING:
            self._advance()
            return Literal(value=tok.text, kind="string")
        if tok.kind is TKind.NUMBER:
            self._advance()
            text = tok.text
            try:
                if "." in text:
                    return Literal(value=float(text), kind="number")
                return Literal(value=int(text), kind="number")
            except ValueError:
                raise TFQLSyntaxError(f"Invalid number: {text!r}", tok.pos)
        if tok.kind is TKind.IDENT and tok.upper == "TRUE":
            self._advance()
            return Literal(value=True, kind="bool")
        if tok.kind is TKind.IDENT and tok.upper == "FALSE":
            self._advance()
            return Literal(value=False, kind="bool")
        if tok.kind is TKind.IDENT and tok.upper == "NULL":
            self._advance()
            return Literal(value=None, kind="null")
        if tok.kind is TKind.IDENT:
            # Bare identifiers are accepted as string values (so users can
            # write `level = ERROR` without quoting). They are still bound as
            # parameters — never spliced into SQL.
            self._advance()
            return Literal(value=tok.text, kind="string")
        raise TFQLSyntaxError(f"Expected value, got {tok.text!r}", tok.pos)


def parse(source: str) -> Query:
    """Tokenize and parse a TFQL program."""
    from traceforge.query.grammar import tokenize

    tokens = tokenize(source)
    return Parser(tokens).parse()
