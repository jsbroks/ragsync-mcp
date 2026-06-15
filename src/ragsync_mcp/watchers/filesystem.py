"""Filesystem watcher: real-time OS file events (folder sources only).

Uses ``watchdog`` to react to create/modify/delete/move events. Each event is
mapped to a ``doc_id`` and reconciled individually via
``SourcePipeline.reindex_document`` — only the affected file is re-indexed.

Events are debounced per-document: editors often emit several writes for one
save, so a short timer coalesces them into a single reindex.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import WatchConfig
from ..ingestion import SourcePipeline
from .base import BaseWatcher

logger = logging.getLogger("ragsync-mcp.watchers.filesystem")

# Coalesce bursts of events for the same file (e.g. editor save churn).
_DEBOUNCE_SECONDS = 0.5


class FilesystemWatcher(BaseWatcher):
    """Watch a folder source's directory for OS-level file changes."""

    def __init__(self, pipeline: SourcePipeline, config: WatchConfig) -> None:
        super().__init__(pipeline, config)
        loader = pipeline.loader
        if not hasattr(loader, "path_to_doc_id"):
            raise TypeError(
                f"filesystem watch requires a folder source; "
                f"source '{pipeline.name}' is type '{pipeline.config.type}'"
            )
        self._root = Path(loader.root)
        self._observer = Observer()
        self._handler = _DebouncingHandler(pipeline, config)

    def start(self) -> None:
        if not self._root.exists():
            logger.warning(
                "cannot watch missing path for source '%s': %s",
                self.pipeline.name,
                self._root,
            )
            return
        self._observer.schedule(
            self._handler, str(self._root), recursive=self.config.recursive
        )
        self._observer.start()
        logger.info("watching filesystem for source '%s' at %s", self.pipeline.name, self._root)

    def stop(self) -> None:
        self._handler.cancel_pending()
        self._observer.stop()
        self._observer.join(timeout=5)


class _DebouncingHandler(FileSystemEventHandler):
    """Translate watchdog events into debounced per-document reindex calls."""

    def __init__(self, pipeline: SourcePipeline, config: WatchConfig) -> None:
        self._pipeline = pipeline
        self._loader = pipeline.loader
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def cancel_pending(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        # Deletion still maps to a doc_id; reindex_document will clear it.
        self._handle(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Treat a move as a delete of the old path and a touch of the new one.
        self._handle(event.src_path, event.is_directory)
        self._handle(getattr(event, "dest_path", None), event.is_directory)

    def _handle(self, path: Optional[str], is_directory: bool) -> None:
        if path is None or is_directory:
            return
        doc_id = self._loader.path_to_doc_id(path)
        if doc_id is None:
            return
        self._schedule(doc_id)

    def _schedule(self, doc_id: str) -> None:
        with self._lock:
            existing = self._timers.get(doc_id)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, self._fire, args=(doc_id,))
            timer.daemon = True
            self._timers[doc_id] = timer
            timer.start()

    def _fire(self, doc_id: str) -> None:
        with self._lock:
            self._timers.pop(doc_id, None)
        self._pipeline.reindex_document(doc_id)
