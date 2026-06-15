"""Polling watcher: re-scan on an interval.

Works for any source type. The fingerprint comparison in
``SourcePipeline.full_reindex`` ensures only changed documents are re-embedded,
so polling a slow-changing source is cheap even at a short interval.
"""

from __future__ import annotations

import logging
import threading

from ..config import WatchConfig
from ..ingestion import SourcePipeline
from .base import BaseWatcher

logger = logging.getLogger("ragsync-mcp.watchers.poll")


class PollWatcher(BaseWatcher):
    """Periodically call ``full_reindex`` on a background thread."""

    def __init__(self, pipeline: SourcePipeline, config: WatchConfig) -> None:
        super().__init__(pipeline, config)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"poll-watch-{self.pipeline.name}", daemon=True
        )
        self._thread.start()
        logger.info(
            "polling source '%s' every %ss",
            self.pipeline.name,
            self.config.interval_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # Wait first so we don't immediately repeat the startup index.
        while not self._stop.wait(self.config.interval_seconds):
            try:
                self.pipeline.full_reindex()
            except Exception:
                # Errors are already recorded on the pipeline status; keep polling.
                logger.exception("poll reindex failed for '%s'", self.pipeline.name)
