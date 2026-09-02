"""Source fingerprinting.

We do not hash multi-GB files entirely. Instead we compute:

- absolute normalized path
- file size
- mtime (nanosecond)
- a stable hash of the first ``SAMPLE_BYTES`` bytes

The sample hash is used by the live tailer to detect *replacement*
(not just append). The tailer compares the first N bytes of the
current file with the first N bytes previously recorded (where N is
``min(previous_size, current_size, SAMPLE_BYTES)``). If they agree,
the file is the same entity. If they disagree, the file has been
replaced by a different file of the same name.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from traceforge.models.events import SourceFingerprint
from traceforge.models.sources import SourceConfig

SAMPLE_BYTES = 64 * 1024
WINDOWS_RESERVED = os.name == "nt"


def _normalize_path(path: str) -> str:
    p = Path(os.fspath(path)).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    else:
        p = p.resolve()
    return os.path.normcase(str(p))


def file_fingerprint(path: str | os.PathLike[str]) -> SourceFingerprint:
    p = Path(os.fspath(path))
    if not p.exists():
        raise FileNotFoundError(f"Source does not exist: {p}")
    st = p.stat()
    sample = b""
    try:
        with open(p, "rb") as f:
            sample = f.read(SAMPLE_BYTES)
    except OSError:
        sample = b""
    sample_hash = hashlib.sha256(sample).hexdigest()
    kind = "text"
    if sample:
        # Crude binary test: many NULs or non-text bytes.
        if b"\x00" in sample[:4096]:
            kind = "binary"
    return SourceFingerprint(
        path=_normalize_path(p),
        size=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        sample_hash=sample_hash,
        content_kind=kind,
    )


def fingerprint_for_source(cfg: SourceConfig) -> SourceFingerprint:
    """Return the current fingerprint for a configured source, or the
    last stored fingerprint if the file no longer exists."""
    try:
        return file_fingerprint(cfg.path)
    except FileNotFoundError:
        if cfg.last_fingerprint is not None:
            return cfg.last_fingerprint
        raise


def prefix_agrees(path: str | os.PathLike[str], recorded_hash: str, recorded_size: int) -> bool:
    """Return True if the current file at ``path`` still has the same
    prefix as ``recorded_hash`` (a hash of the first
    ``min(recorded_size, SAMPLE_BYTES)`` bytes).

    If ``recorded_size`` is 0, returns True.
    """
    if recorded_size <= 0:
        return True
    n = min(recorded_size, SAMPLE_BYTES)
    try:
        with open(path, "rb") as f:
            current_prefix = f.read(n)
    except OSError:
        return False
    if len(current_prefix) < n:
        # File is smaller than recorded — caller will treat as
        # truncation separately; here we conservatively return False
        # to avoid claiming "same prefix" for a smaller file.
        return False
    return hashlib.sha256(current_prefix).hexdigest() == recorded_hash


__all__ = [
    "SAMPLE_BYTES",
    "file_fingerprint",
    "fingerprint_for_source",
    "prefix_agrees",
]
