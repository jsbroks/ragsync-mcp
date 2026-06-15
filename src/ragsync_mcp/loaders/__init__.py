"""Loader registry.

Maps a source ``type`` to its loader class. Adding a source type means adding a
loader here (plus its connection schema in ``config.py``) — no changes to
chunking, embeddings, the vector store, ingestion, or the MCP tools.
"""

from __future__ import annotations

from typing import Type

from ..config import SourceConfig
from .base import BaseLoader, RawDocument, content_hash
from .folder import FolderLoader
from .website import WebsiteLoader

LOADER_REGISTRY: dict[str, Type[BaseLoader]] = {
    "folder": FolderLoader,
    "website": WebsiteLoader,
}


def get_loader(source: SourceConfig) -> BaseLoader:
    """Instantiate the loader for a source, wired to its typed connection."""
    loader_cls = LOADER_REGISTRY.get(source.type)
    if loader_cls is None:
        raise ValueError(
            f"no loader registered for source type '{source.type}'. "
            f"Known types: {sorted(LOADER_REGISTRY)}"
        )
    return loader_cls(source.connection)


__all__ = [
    "BaseLoader",
    "RawDocument",
    "content_hash",
    "FolderLoader",
    "WebsiteLoader",
    "LOADER_REGISTRY",
    "get_loader",
]
