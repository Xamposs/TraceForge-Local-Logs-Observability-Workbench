"""Regression tests for :mod:`traceforge.ingestion.reader`.

Covers exact byte offsets across chunk boundaries, including deliberately
tiny read buffers (4, 7, 11 bytes) and stress sizes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from traceforge.ingestion.reader import iter_lines


def test_simple_full_chunk() -> None:
    f = _write(b"AAA\nBBB\nCCC\n")
    lines = list(iter_lines(f, buffer_bytes=64))
    assert lines == [(0, "AAA", 1), (4, "BBB", 2), (8, "CCC", 3)]


@pytest.mark.parametrize("buffer_bytes", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17, 64, 1024])
def test_buffer_sizes_simple_lf(buffer_bytes: int) -> None:
    text = b"AAA\nBBB\nCCC\nDDD\nEEE\n"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=buffer_bytes))
    # 5 lines, each 3 chars + 1 LF
    expected = []
    for i, name in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        expected.append((i * 4, name, i + 1))
    assert lines == expected


@pytest.mark.parametrize("buffer_bytes", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17, 64, 1024])
def test_buffer_sizes_crlf(buffer_bytes: int) -> None:
    text = b"AAA\r\nBBB\r\nCCC\r\nDDD\r\nEEE\r\n"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=buffer_bytes))
    # 5 lines, each 3 chars + CRLF (2 bytes)
    expected = []
    for i, name in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        expected.append((i * 5, name, i + 1))
    assert lines == expected


def test_partial_final_line_no_newline() -> None:
    text = b"AAA\nBBB"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=4))
    assert lines == [(0, "AAA", 1), (4, "BBB", 2)]


def test_empty_file() -> None:
    f = _write(b"")
    lines = list(iter_lines(f, buffer_bytes=4))
    assert lines == []


def test_only_newlines() -> None:
    f = _write(b"\n\n\n")
    lines = list(iter_lines(f, buffer_bytes=1))
    # Empty lines are emitted with empty text but valid offsets and line numbers.
    assert lines == [(0, "", 1), (1, "", 2), (2, "", 3)]


def test_start_offset_exact() -> None:
    text = b"AAA\nBBB\nCCC\nDDD\nEEE"
    f = _write(text)
    # Start at byte 8 (start of "CCC").
    lines = list(iter_lines(f, start_offset=8, buffer_bytes=3))
    assert lines == [(8, "CCC", 1), (12, "DDD", 2), (16, "EEE", 3)]


def test_start_offset_mid_line_consumes_from_that_point() -> None:
    text = b"AAA\nBBB\nCCC\nDDD\nEEE"
    f = _write(text)
    # Start at byte 10 (mid-line "CCC"). The reader cannot know it was
    # mid-line in the source; it reports whatever bytes are present
    # starting at byte 10. With a 3-byte buffer the first chunk is
    # "C\nD" so the first emitted line is "C" at offset 10, then the
    # remainder of "DDD\nEEE" follows.
    lines = list(iter_lines(f, start_offset=10, buffer_bytes=3))
    assert lines == [(10, "C", 1), (12, "DDD", 2), (16, "EEE", 3)]


def test_long_line_truncation_marker_present() -> None:
    text = b"X" * 5000 + b"\nshort\n"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=64, max_line_bytes=100))
    assert lines[0][0] == 0
    assert lines[0][1].endswith("...[truncated]")
    assert len(lines[0][1]) <= 100 + len("...[truncated]")
    assert lines[1] == (5001, "short", 2)


def test_utf8_multibyte_offsets_are_byte_accurate() -> None:
    # Greek uppercase letters, each 2 bytes in UTF-8.
    text = "ABΓ\nΔEZ\n".encode()
    f = _write(text)
    # Compute expected byte offsets.
    line1 = text.split(b"\n")[0]
    line2 = text.split(b"\n")[1]
    offset_line2 = len(line1) + 1  # +1 for \n
    lines = list(iter_lines(f, buffer_bytes=1))
    assert lines == [
        (0, line1.decode("utf-8"), 1),
        (offset_line2, line2.decode("utf-8"), 2),
    ]


def test_crlf_newline_at_chunk_boundary() -> None:
    # "AAAA\r\nBBB" with a 6-byte buffer: chunk1 = "AAAA\r\n" (6 bytes);
    # chunk2 = "BBB".
    f = _write(b"AAAA\r\nBBB")
    lines = list(iter_lines(f, buffer_bytes=6))
    assert lines == [(0, "AAAA", 1), (6, "BBB", 2)]


def test_newline_exactly_on_chunk_boundary() -> None:
    f = _write(b"AAA\nBBB\nCCC")
    # 4-byte chunks: "AAA\n", "BBB\n", "C", "C".
    lines = list(iter_lines(f, buffer_bytes=4))
    assert lines == [(0, "AAA", 1), (4, "BBB", 2), (8, "CCC", 3)]


def test_line_spans_three_chunks() -> None:
    text = b"AAAAAAAABBBBBBBBCCCCCCCC"  # one long line, no newline
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=7))
    assert lines == [(0, "AAAAAAAABBBBBBBBCCCCCCCC", 1)]


def test_many_short_lines_small_buffer() -> None:
    text = b"\n".join(f"L{i}".encode() for i in range(100)) + b"\n"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=4))
    # Compute expected offsets from the actual text.
    expected = []
    offset = 0
    for i in range(100):
        name = f"L{i}".encode()
        expected.append((offset, name.decode("utf-8"), i + 1))
        offset += len(name) + 1  # +1 for the '\n'
    assert lines == expected


def test_truncation_does_not_corrupt_subsequent_offsets() -> None:
    text = b"x" * 50 + b"\nshort\n"
    f = _write(text)
    lines = list(iter_lines(f, buffer_bytes=10, max_line_bytes=20))
    assert lines[0][0] == 0
    assert lines[0][1].endswith("...[truncated]")
    assert lines[1] == (51, "short", 2)


def _write(content: bytes) -> Path:
    import tempfile

    d = tempfile.mkdtemp(prefix="tf-reader-")
    p = Path(d) / "x.log"
    p.write_bytes(content)
    return p
