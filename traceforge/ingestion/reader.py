"""Streaming line reader.

We do not load an entire large file into Python memory. The reader returns
decoded lines one at a time using a fixed-size buffer.

Every yielded ``byte_offset`` is the EXACT absolute byte position of the
first byte of that logical line in the source file. This is required for
deterministic event IDs (see :mod:`traceforge.ingestion.ids`) and for
accurate live-tail offset tracking.

Line numbers are also exact: we count newlines emitted.
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

    ``start_offset`` is the absolute byte position at which to begin reading.
    The first yielded line has ``line_number=1``; line numbers always count
    from 1 regardless of ``start_offset`` (line numbers are informational
    and are NOT used for event identity).

    Lines longer than ``max_line_bytes`` are truncated; the yielded text ends
    with a literal ``\"...[truncated]\"`` marker.

    CR (``\\r``) is stripped from the end of each line. LF (``\\n``) is the
    line terminator. The final unterminated line is emitted as-is on EOF.
    """
    p = Path(os.fspath(path))
    if not p.exists():
        return
    line_no = 1
    leftover = b""
    # ``pos`` is the absolute file offset of the first byte of the data we
    # are about to process in the next iteration (i.e. data[0] sits at
    # position ``pos`` in the file).
    pos = max(0, int(start_offset))
    with open(p, "rb") as f:
        if pos:
            f.seek(pos)
        while True:
            chunk = f.read(max(1, int(buffer_bytes)))
            if not chunk:
                if leftover:
                    text = leftover.decode("utf-8", errors="replace")
                    if len(text) > max_line_bytes:
                        text = text[:max_line_bytes] + "...[truncated]"
                    yield pos, text.rstrip("\n\r"), line_no
                return
            data = leftover + chunk
            data_len = len(data)
            start = 0
            i = 0
            while i < data_len:
                b = data[i]
                if b == 0x0A:  # \n
                    line_bytes = data[start:i]
                    if line_bytes.endswith(b"\r"):
                        line_bytes = line_bytes[:-1]
                    text = line_bytes.decode("utf-8", errors="replace")
                    if len(text) > max_line_bytes:
                        text = text[:max_line_bytes] + "...[truncated]"
                    # ``pos + start`` is the absolute file offset of the
                    # first byte of this logical line.
                    yield pos + start, text, line_no
                    line_no += 1
                    i += 1
                    start = i
                else:
                    i += 1
            # Any bytes beyond the last newline are a partial line; they
            # become the leftover for the next iteration. Their absolute
            # file offset is ``pos + start``.
            leftover = data[start:]
            pos = pos + start
    return


__all__ = ["DEFAULT_BUFFER_BYTES", "MAX_LINE_BYTES", "iter_lines"]
