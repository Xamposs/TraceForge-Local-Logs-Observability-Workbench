"""Live tailer.

Tracks per-source byte offsets in memory and ingests only newly-appended
content via the streaming pipeline. Implements:

* **Append.** A growing file with the same first ``SAMPLE_BYTES`` bytes
  is an append, not a replacement. Offset advances monotonically.
* **Truncation.** ``size < offset`` resets offset to zero.
* **Replacement.** The inode (POSIX) or file ID (Windows) changes, OR
  the *first* ``SAMPLE_BYTES`` bytes differ from the recorded prefix,
  AND the file is smaller than the previous size: this is treated as
  replacement and offset resets to zero.
* **Rotation (renamed/moved away).** The watcher detects the move; the
  tailer marks the source as missing and stops reading the old path.
  Auto-follow of a freshly created file at the original path is not
  attempted — operators must add the rotated file as a new source.

The tailer also buffers a partial trailing line per source. A logical
line is only emitted once a newline is observed; the next append
appends to the buffer rather than starting a new event.

Offset semantics
-----------------
The tailer tracks an absolute ``state.offset`` measured in bytes from
the start of the source file. ``state.pending_offset`` is the absolute
position of the *first* byte of the partial trailing line that has not
yet been emitted. ``state.offset`` is only ever advanced to
``state.pending_offset`` after the new bytes have been successfully
ingested. Cancellation, parse error, or database error therefore leave
``state.offset`` at the last safely consumed/committed byte.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from traceforge.ingestion.fingerprint import file_fingerprint
from traceforge.ingestion.pipeline import CancellationToken, IngestionProgress
from traceforge.live.watcher import FileWatcher, WatchEvent
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database

SAMPLE_BYTES = 64 * 1024


@dataclass
class TailerState:
    path: str
    source_id: int
    # File identity.
    sample_hash: str = ""
    size_at_open: int = 0
    inode: int | None = None
    # Tail position.
    offset: int = 0  # absolute byte position of the next byte to read
    # Partial trailing line (no newline yet).
    pending_bytes: bytes = b""
    pending_offset: int = 0
    last_event_at: float = 0.0
    missing: bool = False
    last_known_size: int = 0


ProgressFn = Callable[[TailerState, IngestionProgress], None]


def _file_identity(path: str) -> tuple[int | None, int, str]:
    """Return (inode-or-None, size, sample_hash) for ``path``."""
    p = Path(path)
    st = p.stat()
    try:
        inode = st.st_ino
    except AttributeError:
        inode = None
    fp = file_fingerprint(path)
    return inode, int(st.st_size), fp.sample_hash


def _is_replacement(state: TailerState, inode: int | None, size: int, sample: str) -> bool:
    """Decide whether the file has been replaced (not merely appended).

    Replacement is signalled by any of:
    * inode changed (POSIX) — different file entity
    * the file shrank below ``state.size_at_open``
    * the *prefix* of the current file does not match the recorded
      sample hash (the prefix-agreement test). A normal append
      preserves the prefix, so a mismatch reliably indicates a
      replacement.
    """
    if state.inode is not None and inode is not None and inode != state.inode:
        return True
    if size < state.size_at_open:
        return True
    if state.sample_hash and sample and sample != state.sample_hash:
        # Confirm the prefix actually disagrees. The sample hash
        # discrepancy could be a side effect of an append to a small
        # file (which moves the prefix boundary). We compute the
        # current file's prefix hash and compare.
        n = min(state.size_at_open, SAMPLE_BYTES)
        try:
            with open(state.path, "rb") as f:
                current_prefix = f.read(n)
        except OSError:
            return True
        if hashlib.sha256(current_prefix).hexdigest() != state.sample_hash:
            return True
    return False


def _hash_file_prefix(path: str, n: int) -> str:
    import hashlib

    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(n))
    except OSError:
        return ""
    return h.hexdigest()


class LiveTailer:
    def __init__(
        self,
        db: Database,
        *,
        poll_interval: float = 0.5,
        on_progress: ProgressFn | None = None,
    ) -> None:
        self._db = db
        self._watcher = FileWatcher()
        self._lock = threading.Lock()
        self._states: dict[str, TailerState] = {}
        self._cancels: dict[str, CancellationToken] = {}
        self._poll = poll_interval
        self._on_progress = on_progress
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._new_event_subscribers: list[Callable[[str, list], None]] = []

    # ---- subscriptions ----

    def subscribe_events(self, fn: Callable[[str, list], None]) -> None:
        with self._lock:
            self._new_event_subscribers.append(fn)

    def _publish_events(self, source_path: str, events: list[Any]) -> None:
        for fn in list(self._new_event_subscribers):
            with contextlib.suppress(Exception):
                fn(source_path, events)

    # ---- watch control ----

    def add_source(
        self,
        source_id: int,
        cfg: SourceConfig,
        *,
        start_offset: int = 0,
        start_from_end: bool = False,
    ) -> TailerState:
        """Add a source to the tailer.

        ``start_offset`` is the absolute byte position at which to begin
        reading. ``start_from_end=True`` snaps the offset to the
        current file size (i.e. ignore existing content; useful for
        ``Watch File`` on a never-ingested file).

        The first observed ``size_at_open`` is used by the
        replacement detector.
        """
        state = TailerState(path=cfg.path, source_id=source_id)
        try:
            inode, size, sample = _file_identity(cfg.path)
            state.inode = inode
            state.sample_hash = sample
            state.size_at_open = size
            state.last_known_size = size
            if start_from_end:
                state.offset = size
            else:
                state.offset = min(max(0, start_offset), size)
        except FileNotFoundError:
            state.missing = True
        with self._lock:
            self._states[cfg.path] = state
        self._watcher.watch_path(cfg.path, self._on_watch_event)
        return state

    def remove_source(self, path: str) -> None:
        with self._lock:
            self._states.pop(path, None)
            cancel = self._cancels.pop(path, None)
        if cancel is not None:
            cancel.cancel()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="traceforge-tailer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for cancel in self._cancels.values():
                cancel.cancel()
        with contextlib.suppress(Exception):
            self._watcher.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---- internal ----

    def _on_watch_event(self, ev: WatchEvent) -> None:
        with self._lock:
            state = self._states.get(ev.path)
        if state is None:
            return
        if ev.kind in ("deleted", "moved"):
            state.missing = True
            return
        # Once missing, do not un-mark on a "created" event: a freshly
        # created file at the original path is a new source and must
        # not be auto-followed.
        if state.missing:
            return
        # Defer actual ingest to the poll loop to avoid hot re-entrancy.

    def _run(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(Exception):
                self._tick()
            time.sleep(self._poll)

    def _tick(self) -> None:
        with self._lock:
            items = list(self._states.items())
        for path, state in items:
            if state.missing:
                # Once a source has been marked missing (file was
                # deleted, moved, or rotated away), we do NOT
                # auto-follow a freshly created file at the same
                # path. The user must add it as a new source.
                continue
            try:
                inode, size, sample = _file_identity(path)
            except FileNotFoundError:
                state.missing = True
                continue
            previous_size = state.last_known_size
            previous_offset = state.offset
            # An inode change means the file at this path is a
            # different entity (e.g. after a rotation/replace). We
            # mark the source missing rather than auto-following.
            if state.inode is not None and inode is not None and inode != state.inode:
                state.missing = True
                return
            replacement = _is_replacement(state, inode, size, sample)
            truncated = size < previous_size or size < previous_offset
            if replacement or truncated:
                # The file shrunk or was replaced; reset to the
                # beginning of the new content.
                state.offset = 0
                state.pending_bytes = b""
                state.pending_offset = 0
            if state.inode is None or replacement:
                state.inode = inode
            state.sample_hash = sample
            state.last_known_size = size
            if size == state.offset and not state.pending_bytes:
                continue
            self._ingest_new_bytes(state, size)

    def _ingest_new_bytes(self, state: TailerState, size: int) -> None:
        """Stream newly-appended bytes through the pipeline.

        Holds a per-source ``pending_bytes`` buffer for any trailing
        line that has not yet been terminated with a newline. The
        committed offset is only advanced to
        ``state.pending_offset`` after ingestion succeeds.
        """
        cancel = CancellationToken()
        with self._lock:
            self._cancels[state.path] = cancel
        try:
            with open(state.path, "rb") as f:
                f.seek(state.offset)
                tail = f.read(max(0, size - state.offset))
            if not tail and not state.pending_bytes:
                return
            combined = state.pending_bytes + tail
            last_nl = combined.rfind(b"\n")
            if last_nl == -1:
                # No terminator yet; keep the entire slice pending.
                state.pending_bytes = combined
                state.pending_offset = state.offset
                return
            to_ingest = combined[: last_nl + 1]
            new_pending = combined[last_nl + 1 :]
            new_pending_offset = state.offset + len(to_ingest)
            # We are about to commit an ingest of ``to_ingest`` starting
            # at ``state.offset``. The byte offsets of emitted events
            # must use absolute file positions, so we feed the slice
            # through the in-memory path. The pipeline dedup-by-PK
            # ensures re-ingest safety.
            try:
                self._ingest_slice(state, to_ingest, cancel)
            except Exception:
                # Ingest failed: keep state untouched so the next tick
                # will retry.
                raise
            else:
                state.offset = new_pending_offset
                state.pending_bytes = new_pending
                state.pending_offset = new_pending_offset
                state.last_event_at = time.time()
        finally:
            with self._lock:
                if self._cancels.get(state.path) is cancel:
                    self._cancels.pop(state.path, None)

    def _ingest_slice(
        self,
        state: TailerState,
        byte_slice: bytes,
        cancel: CancellationToken,
    ) -> None:
        from traceforge.ingestion.pipeline import parse_bytes_to_events
        from traceforge.models.sources import SourceConfig

        cfg = SourceConfig(path=state.path, alias=state.path)
        events = parse_bytes_to_events(
            self._db,
            cfg,
            data=byte_slice,
            start_offset=state.offset,
            source_id=state.source_id,
            cancel=cancel,
        )
        if events:
            self._publish_events(state.path, events)
