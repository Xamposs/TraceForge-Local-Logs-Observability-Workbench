"""Live tailer.

Tracks per-source byte offsets in memory; on file modification it ingests
only the new bytes via the streaming pipeline. Detects:

* file appended  -> advance offset
* file truncated -> reset offset to 0
* file replaced  -> reset offset to 0 and re-ingest
* file deleted   -> mark source as missing
* file rotated   -> the user is expected to add a new source for the new
  file; the tailer does not auto-follow across renames.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from traceforge.ingestion.fingerprint import file_fingerprint
from traceforge.ingestion.pipeline import (
    CancellationToken,
    IngestionProgress,
    ingest_file,
)
from traceforge.live.watcher import FileWatcher, WatchEvent
from traceforge.models.sources import SourceConfig
from traceforge.storage import Database


@dataclass
class TailerState:
    path: str
    source_id: int
    offset: int = 0
    sample_hash: str = ""
    last_event_at: float = 0.0
    missing: bool = False


ProgressFn = Callable[[TailerState, IngestionProgress], None]


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
        self._event_buffer: list[tuple[str, list[Any]]] = []
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

    def add_source(self, source_id: int, cfg: SourceConfig) -> TailerState:
        state = TailerState(path=cfg.path, source_id=source_id)
        try:
            fp = file_fingerprint(cfg.path)
            state.sample_hash = fp.sample_hash
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
        if ev.kind == "deleted":
            state.missing = True
            return
        # Defer actual ingest to the poll loop to avoid hot re-entrancy.
        state.missing = False

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
                if Path(path).exists():
                    state.missing = False
                else:
                    continue
            try:
                fp = file_fingerprint(path)
            except FileNotFoundError:
                state.missing = True
                continue
            size = fp.size
            if state.sample_hash and fp.sample_hash != state.sample_hash:
                # File replaced; restart from 0.
                state.offset = 0
                state.sample_hash = fp.sample_hash
            if size < state.offset:
                # truncated
                state.offset = 0
            if size == state.offset:
                continue
            self._ingest_new_bytes(state, size)

    def _ingest_new_bytes(self, state: TailerState, size: int) -> None:
        cancel = CancellationToken()
        with self._lock:
            self._cancels[state.path] = cancel
        try:
            cfg = SourceConfig(path=state.path, alias=state.path)
            result = ingest_file(
                self._db,
                cfg,
                source_id=state.source_id,
                start_offset=state.offset,
                cancel=cancel,
                on_events=lambda evs: self._publish_events(state.path, evs),
            )
            state.offset = size
            state.last_event_at = time.time()
            if self._on_progress is not None:
                with contextlib.suppress(Exception):
                    self._on_progress(state, result.progress)
        finally:
            with self._lock:
                if self._cancels.get(state.path) is cancel:
                    self._cancels.pop(state.path, None)
