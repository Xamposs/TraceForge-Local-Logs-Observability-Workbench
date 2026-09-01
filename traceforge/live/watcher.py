"""File-system watcher.

A small, deterministic wrapper around :mod:`watchdog` that emits high-level
file events (``created``, ``modified``, ``deleted``, ``moved``) to a Python
callback. We use the polling observer as a safe fallback on platforms where
the native observer is unreliable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except Exception:  # pragma: no cover - watchdog always available in dev
    Observer = None  # type: ignore[assignment]
    FileSystemEvent = None  # type: ignore[assignment]
    FileSystemEventHandler = None  # type: ignore[assignment]


@dataclass
class WatchEvent:
    path: str
    kind: str  # "modified" | "created" | "deleted" | "moved"


class _Handler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, cb: Callable[[WatchEvent], None]) -> None:
        self._cb = cb

    def on_modified(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._cb(WatchEvent(path=event.src_path, kind="modified"))

    def on_created(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._cb(WatchEvent(path=event.src_path, kind="created"))

    def on_deleted(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._cb(WatchEvent(path=event.src_path, kind="deleted"))

    def on_moved(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._cb(WatchEvent(path=event.dest_path, kind="moved"))


class FileWatcher:
    def __init__(self) -> None:
        self._observer = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def watch_path(self, path: str, callback: Callable[[WatchEvent], None]) -> None:
        if Observer is None:
            raise RuntimeError("watchdog is not available")
        p = Path(path)
        parent = p if p.is_dir() else p.parent
        handler = _Handler(callback)
        with self._lock:
            if self._observer is None:
                self._observer = Observer()
                self._observer.start()
            self._observer.schedule(handler, str(parent), recursive=False)

    def close(self) -> None:
        with self._lock:
            if self._observer is not None:
                try:
                    self._observer.stop()
                    self._observer.join(timeout=2.0)
                except Exception:
                    pass
                self._observer = None
            self._stopped.set()
