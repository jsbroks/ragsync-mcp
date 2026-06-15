"""Loader interface and the ``RawDocument`` contract.

A loader reads one source's origin and yields ``RawDocument`` objects. The
pipeline depends only on this interface; adding a source type is a new loader
plus a registry entry (see ``loaders/__init__.py``), nothing else.

RawDocument invariants (critical — see AGENTS.md):

  - ``doc_id`` is unique within the source, stable across re-indexes, and
    reversible: ``load_one(doc_id)`` fetches exactly that document.
  - ``fingerprint`` changes iff the content changes (a content hash). This
    drives incremental indexing — unchanged docs are skipped.
  - ``metadata`` is stamped onto every chunk; keep it small and JSON-serializable.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class RawDocument:
    """One document as produced by a loader, before chunking/embedding."""

    doc_id: str
    content: str
    metadata: dict = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        # Default the fingerprint to a content hash so loaders that don't have
        # a cheaper change signal still get correct incremental behaviour.
        if not self.fingerprint:
            self.fingerprint = content_hash(self.content)


def content_hash(content: str) -> str:
    """Stable SHA-256 of text content, for use as a fingerprint."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class BaseLoader(ABC):
    """Abstract source reader.

    Subclasses receive the already-validated, typed connection model for their
    source type and implement the two read paths used by the pipeline.
    """

    def __init__(self, connection) -> None:
        self.connection = connection

    @abstractmethod
    def load_all(self) -> Iterator[RawDocument]:
        """Yield every document currently in the source."""

    @abstractmethod
    def load_one(self, doc_id: str) -> Optional[RawDocument]:
        """Fetch a single document by id, or ``None`` if it no longer exists."""
