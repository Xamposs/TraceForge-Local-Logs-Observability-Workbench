"""TFQL lexer.

Produces a flat list of typed tokens. Whitespace and ``#`` line comments are
skipped. Strings support double-quotes with ``\\`` and ``\\\"`` escapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TKind(Enum):
    # Literals
    NUMBER = auto()
    STRING = auto()
    IDENT = auto()
    BOOL = auto()
    # Symbols
    LPAREN = auto()
    RPAREN = auto()
    PIPE = auto()
    COMMA = auto()
    # Operators (multi-character first)
    GE = auto()
    LE = auto()
    NE = auto()
    EQ = auto()
    GT = auto()
    LT = auto()
    EOF = auto()


KEYWORDS = {
    "AND": "AND",
    "OR": "OR",
    "NOT": "NOT",
    "IN": "IN",
    "CONTAINS": "CONTAINS",
    "STARTS_WITH": "STARTS_WITH",
    "ENDS_WITH": "ENDS_WITH",
    "TRUE": "TRUE",
    "FALSE": "FALSE",
    "NULL": "NULL",
    "SORT": "SORT",
    "LIMIT": "LIMIT",
    "STATS": "STATS",
    "BY": "BY",
    "ASC": "ASC",
    "DESC": "DESC",
}

WORD_OPS = {"CONTAINS", "STARTS_WITH", "ENDS_WITH"}


@dataclass(frozen=True)
class Token:
    kind: TKind
    text: str
    pos: int
    upper: str = ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.kind.name}, {self.text!r}, pos={self.pos})"


class TFQLSyntaxError(Exception):
    def __init__(self, message: str, pos: int | None = None, length: int = 1) -> None:
        self.pos = pos
        self.length = length
        if pos is not None:
            super().__init__(f"{message} (at position {pos})")
        else:
            super().__init__(message)


def tokenize(source: str) -> list[Token]:
    src = source
    n = len(src)
    i = 0
    tokens: list[Token] = []
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "-" and i + 1 < n and src[i + 1] == "-":
            # SQL-style line comment, kept for robustness against
            # injection-style query text. We just skip the rest of the line.
            while i < n and src[i] != "\n":
                i += 1
            continue
        start = i
        if c.isalpha() or c == "_" or c == "@":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "_.-:"):
                j += 1
            text = src[i:j]
            upper = text.upper()
            if upper in KEYWORDS:
                if upper in WORD_OPS:
                    tokens.append(Token(TKind.IDENT, text, start, upper))
                else:
                    tokens.append(Token(TKind.IDENT, text, start, upper))
            else:
                tokens.append(Token(TKind.IDENT, text, start, upper))
            i = j
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            if c == "-":
                j = i + 2
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            tokens.append(Token(TKind.NUMBER, src[i:j], start))
            i = j
            continue
        if c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                ch = src[j]
                if ch == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    if nxt == '"':
                        buf.append('"')
                    elif nxt == "\\":
                        buf.append("\\")
                    elif nxt == "n":
                        buf.append("\n")
                    elif nxt == "t":
                        buf.append("\t")
                    else:
                        buf.append(nxt)
                    j += 2
                    continue
                if ch == '"':
                    break
                buf.append(ch)
                j += 1
            if j >= n:
                raise TFQLSyntaxError("Unterminated string literal", start, j - start)
            tokens.append(Token(TKind.STRING, "".join(buf), start))
            i = j + 1
            continue
        # Symbols (two-character operators checked first).
        if c in "<>!":
            two = src[i : i + 2]
            if c == "!" and i + 1 < n and src[i + 1] == "=":
                tokens.append(Token(TKind.NE, two, start))
                i += 2
                continue
            if c == ">" and i + 1 < n and src[i + 1] == "=":
                tokens.append(Token(TKind.GE, two, start))
                i += 2
                continue
            if c == "<" and i + 1 < n and src[i + 1] == "=":
                tokens.append(Token(TKind.LE, two, start))
                i += 2
                continue
        if c == "(":
            tokens.append(Token(TKind.LPAREN, c, start))
            i += 1
            continue
        if c == ")":
            tokens.append(Token(TKind.RPAREN, c, start))
            i += 1
            continue
        if c == "|":
            tokens.append(Token(TKind.PIPE, c, start))
            i += 1
            continue
        if c == ",":
            tokens.append(Token(TKind.COMMA, c, start))
            i += 1
            continue
        if c == "=":
            tokens.append(Token(TKind.EQ, c, start))
            i += 1
            continue
        if c == ">":
            tokens.append(Token(TKind.GT, c, start))
            i += 1
            continue
        if c == "<":
            tokens.append(Token(TKind.LT, c, start))
            i += 1
            continue
        raise TFQLSyntaxError(f"Unexpected character: {c!r}", start, 1)
    tokens.append(Token(TKind.EOF, "", n, ""))
    return tokens
