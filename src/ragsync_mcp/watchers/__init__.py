"""Watcher registry.

Maps a watch ``mode`` to its watcher class. ``build_watcher`` returns ``None``
when watching is disabled, so the server can call it uniformly per source.
Adding a watch strategy (e.g. webhook-driven ``event``) is a new watcher here.
"""

from __future__ import annotations

from typing import Optional, Type

from ..ingestion import SourcePipeline
from .base import BaseWatcher
from .filesystem import FilesystemWatcher
from .poll import PollWatcher

WATCHER_REGISTRY: dict[str, Type[BaseWatcher]] = {
    "filesystem": FilesystemWatcher,
    "poll": PollWatcher,
}


def build_watcher(pipeline: SourcePipeline) -> Optional[BaseWatcher]:
    """Construct the watcher for a pipeline, or ``None`` if watching is off."""
    watch = pipeline.config.watch
    if not watch.enabled:
        return None
    watcher_cls = WATCHER_REGISTRY.get(watch.mode)
    if watcher_cls is None:
        raise ValueError(
            f"no watcher registered for mode '{watch.mode}'. "
            f"Known modes: {sorted(WATCHER_REGISTRY)}"
        )
    return watcher_cls(pipeline, watch)


__all__ = [
    "BaseWatcher",
    "FilesystemWatcher",
    "PollWatcher",
    "WATCHER_REGISTRY",
    "build_watcher",
]
