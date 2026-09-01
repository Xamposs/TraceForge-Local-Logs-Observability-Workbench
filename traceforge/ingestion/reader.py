"""Streaming line reader.

We do not load an entire large file into Python memory. The reader returns
decoded lines one at a time using a fixed-size buffer and the ``io`` module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

DEFAULT_BUFFER_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_LINE_BYTES = 1_000_000  # 1 MB per line cap


def iter_lines(
    path: str | os.PathLike[str],
    *,
    start_offset: int = 0,
    buffer_bytes: int = DEFAULT_BUFFER_BYTES,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> Iterator[tuple[int, str, int]]:
    """Yield ``(byte_offset, line_text, line_number)`` for each line in the file.

    Starts reading from ``start_offset`` (0 for full re-ingest). Strips the
    trailing newline. Lines longer than ``max_line_bytes`` are truncated.
    """
    p = Path(os.fspath(path))
    if not p.exists():
        return
    line_no = 1
    leftover = b""
    pos = 0
    with open(p, "rb") as f:
        if start_offset:
            f.seek(start_offset)
            pos = start_offset
            line_no = max(1, _approx_line_for_offset(p, start_offset))
        while True:
            chunk = f.read(buffer_bytes)
            if not chunk:
                if leftover:
                    text = leftover.decode("utf-8", errors="replace")
                    if len(text) > max_line_bytes:
                        text = text[:max_line_bytes] + "...[truncated]"
                    yield pos, text.rstrip("\n\r"), line_no
                break
            data = leftover + chunk
            start = 0
            for i, b in enumerate(data):
                if b == 0x0A:  # \n
                    line_bytes = data[start:i]
                    if line_bytes.endswith(b"\r"):
                        line_bytes = line_bytes[:-1]
                    try:
                        text = line_bytes.decode("utf-8", errors="replace")
                    except Exception:  # pragma: no cover - extremely defensive
                        text = line_bytes.decode("utf-8", errors="replace")
                    if len(text) > max_line_bytes:
                        text = text[:max_line_bytes] + "...[truncated]"
                    yield pos + start, text, line_no
                    line_no += 1
                    start = i + 1
            leftover = data[start:]
            pos += len(chunk) - len(leftover)
    return


def _approx_line_for_offset(p: Path, offset: int) -> int:
    """Return an approximate starting line number for an offset.

    We do not seek-line-perfect to avoid an extra full read; the inaccuracy
    is acceptable because line_number is informational, not a primary key.
    """
    if offset <= 0:
        return 1
    try:
        # Average log line ~ 200 bytes is a reasonable default.
        return max(1, offset // 200)
    except Exception:
        return 1
