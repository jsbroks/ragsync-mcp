"""Watcher interface.

A watcher keeps a source's index fresh after the initial build. It owns a
background thread; ``start`` begins watching and returns immediately, ``stop``
tears the thread down cleanly. Watchers only ever call back into the pipeline's
reconciliation methods (``reindex_document`` / ``full_reindex``); they never
touch the loader or vector store directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import WatchConfig
from ..ingestion import SourcePipeline


class BaseWatcher(ABC):
    """Abstract change watcher bound to one pipeline."""

    def __init__(self, pipeline: SourcePipeline, config: WatchConfig) -> None:
        self.pipeline = pipeline
        self.config = config

    @abstractmethod
    def start(self) -> None:
        """Begin watching in the background (non-blocking)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop watching and release resources."""
