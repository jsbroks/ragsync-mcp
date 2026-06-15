"""Watcher for the config file itself, enabling live reload.

Watches the directory containing the config file (editors often replace a file
via a temp-file rename, so watching the file inode directly is unreliable) and
fires a debounced callback when the config path is created, modified, or moved
into place. The callback does the actual reload — this module only detects
"the config changed".
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("ragsync-mcp.config_watch")

_DEBOUNCE_SECONDS = 0.5


class ConfigFileWatcher:
    """Invoke ``on_change`` shortly after the config file is modified."""

    def __init__(self, config_path: str, on_change: Callable[[], None]) -> None:
        self._path = Path(config_path).resolve()
        self._on_change = on_change
        self._observer = Observer()
        self._handler = _ConfigEventHandler(self._path, self._fire)
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        directory = self._path.parent
        if not directory.exists():
            logger.warning("cannot watch config dir (missing): %s", directory)
            return
        self._observer.schedule(self._handler, str(directory), recursive=False)
        self._observer.start()
        logger.info("watching config file for changes: %s", self._path)

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._observer.stop()
        self._observer.join(timeout=5)

    def _fire(self) -> None:
        # Debounce bursts (temp write + rename) into a single reload.
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._on_change()
        except Exception:
            logger.exception("config reload callback failed")


class _ConfigEventHandler(FileSystemEventHandler):
    """Fire only for events that touch the watched config path."""

    def __init__(self, path: Path, fire: Callable[[], None]) -> None:
        self._path = path
        self._fire = fire

    def _matches(self, raw_path) -> bool:
        if raw_path is None:
            return False
        try:
            return Path(raw_path).resolve() == self._path
        except OSError:
            return False

    def on_created(self, event: FileSystemEvent) -> None:
        if self._matches(event.src_path):
            self._fire()

    def on_modified(self, event: FileSystemEvent) -> None:
        if self._matches(event.src_path):
            self._fire()

    def on_moved(self, event: FileSystemEvent) -> None:
        if self._matches(getattr(event, "dest_path", None)):
            self._fire()
