"""Deterministic error-signature extraction.

Goal: group recurring errors that differ only in dynamic / variable parts
(numbers, UUIDs, hex IDs, timestamps, long identifiers).

Algorithm:

1. Strip ANSI escape sequences.
2. Normalize obvious stack-trace-style lines (collapse ``File "..."`` frames
   to a stable placeholder).
3. Replace the following with a stable placeholder:
   * ISO-8601 timestamps
   * UUIDs
   * hex IDs (>=8 hex chars)
   * decimal numbers
   * memory addresses (``0x...``)
   * long identifiers (>= 12 chars, looks like a token)
4. Collapse repeated whitespace.

This is intentionally conservative. We do not attempt fuzzy matching,
semantic grouping, or AI summarization. The signature is a string; equality
of signatures is the only grouping key.
"""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_LONG = re.compile(r"[0-9a-fA-F]{16,}")
_FLOAT = re.compile(r"\d+\.\d+")
_INT = re.compile(r"\d{2,}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{16,}")
_QUOTED = re.compile(r'"[^"\n]{6,}"')
_FILE = re.compile(r'File "[^"]+", line \d+')
_MEMORY = re.compile(r"0x[0-9a-fA-F]+")


def _collapse_numeric(s: str) -> str:
    """Replace digit runs (length >= 2) with ``<num>``.

    We avoid splitting alphanumeric identifiers like ``abc123`` by requiring
    the character before the run to be a non-alphanumeric (or start of
    string). The character *after* the run may be a letter (so that
    ``"after 5012ms"`` becomes ``"after <num>ms"``), but only when the run
    is preceded by a non-alphanumeric.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            run = s[i:j]
            prev_is_alnum = i > 0 and (s[i - 1].isalnum() or s[i - 1] == "_")
            if len(run) >= 2 and not prev_is_alnum:
                out.append("<num>")
            else:
                out.append(run)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _collapse_hex(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "0123456789abcdefABCDEF":
            j = i
            while j < n and s[j] in "0123456789abcdefABCDEF":
                j += 1
            run = s[i:j]
            prev_is_letter = i > 0 and s[i - 1].isalpha()
            next_is_letter = j < n and s[j].isalpha()
            if len(run) >= 16 and not prev_is_letter and not next_is_letter:
                out.append("<hex>")
            else:
                out.append(run)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _collapse_tokens(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isalnum() or ch in "_-":
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_-"):
                j += 1
            run = s[i:j]
            if len(run) >= 16:
                out.append("<id>")
            else:
                out.append(run)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def normalize_signature(message: str, max_len: int = 240) -> str:
    """Return a stable signature for a (potentially dynamic) error message."""
    if message is None:
        return ""
    s = message
    s = _ANSI.sub("", s)
    s = _ISO.sub("<ts>", s)
    s = _UUID.sub("<uuid>", s)
    s = _MEMORY.sub("<addr>", s)
    s = _collapse_hex(s)
    # Floats must run before generic integer collapse to avoid leaving
    # trailing decimals.
    s = _FLOAT.sub("<num>", s)
    s = _collapse_numeric(s)
    s = _FILE.sub("File <path>", s)
    s = _QUOTED.sub('"<str>"', s)
    s = _collapse_tokens(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s


def group_messages(messages: list[str]) -> dict[str, list[str]]:
    """Return a dict mapping signature -> list of original messages."""
    out: dict[str, list[str]] = {}
    for m in messages:
        sig = normalize_signature(m)
        out.setdefault(sig, []).append(m)
    return out
