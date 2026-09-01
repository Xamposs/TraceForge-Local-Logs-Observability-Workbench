"""Source fingerprinting.

We do not hash multi-GB files entirely. Instead we compute:

- absolute normalized path
- file size
- mtime (nanosecond)
- a stable hash of the first ``SAMPLE_BYTES`` bytes

That is enough to detect: same file reopened, file changed, file truncated
(when size shrinks), file replaced (size+mtime change), and a partial
content change.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from traceforge.models.events import SourceFingerprint
from traceforge.models.sources import SourceConfig

SAMPLE_BYTES = 64 * 1024
WINDOWS_RESERVED = os.name == "nt"


def _normalize_path(path: str | os.PathLike[str]) -> str:
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
    """Return the current fingerprint for a configured source, or the last
    stored fingerprint if the file no longer exists."""
    try:
        return file_fingerprint(cfg.path)
    except FileNotFoundError:
        if cfg.last_fingerprint is not None:
            return cfg.last_fingerprint
        raise
