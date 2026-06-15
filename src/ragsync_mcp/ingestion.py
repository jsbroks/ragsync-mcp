"""Per-source ingestion pipeline.

``SourcePipeline`` is the one object that ties a source's loader, chunker,
embedder, and vector store together and is the *only* thing the MCP tool layer
talks to. Data flows one way: ``loader -> chunker -> embedder -> vector store``.

Incremental indexing is driven by fingerprints stored on each chunk:

  - ``full_reindex`` scans the source, (re)indexes new/changed documents (by
    fingerprint), and deletes documents that have disappeared.
  - ``reindex_document`` handles a single add/change/delete (used by watchers).

Both mutate the store under a per-pipeline lock so background watchers and tool
calls can't corrupt each other.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .chunking import chunk_text
from .config import SourceConfig
from .embeddings import Embedder, get_embedder
from .loaders import RawDocument, get_loader
from .vectorstore import ChromaStore

logger = logging.getLogger("ragsync-mcp.ingestion")


class SourcePipeline:
    """Owns one source end to end: load, chunk, embed, index, search."""

    def __init__(self, config: SourceConfig):
        self.config = config
        self.name = config.name
        self.loader = get_loader(config)
        self.embedder: Embedder = get_embedder(config.embedding)
        self.store = ChromaStore(
            persist_directory=config.vector_store.persist_directory,
            collection=config.vector_store.collection,
            embedding_signature=self.embedder.signature,
        )

        self._lock = threading.Lock()
        # Mutable status surfaced via get_index_status / list_sources.
        self._indexing = False
        self._last_indexed_at: Optional[float] = None
        self._last_error: Optional[str] = None

    # -- indexing -----------------------------------------------------------

    def initial_index(self) -> None:
        """Run the first index at startup. Errors are recorded, not raised."""
        try:
            self.full_reindex()
        except Exception as exc:  # one bad source must not crash the server
            logger.exception("initial index failed for source '%s'", self.name)
            with self._lock:
                self._last_error = str(exc)

    def full_reindex(self) -> dict:
        """Reconcile the index with a fresh full scan of the source.

        New and changed documents (by fingerprint) are re-embedded; documents
        that vanished from the source are deleted. Returns the post-run status.
        """
        with self._lock:
            self._indexing = True
        try:
            stored = self.store.fingerprint_map()
            seen: set[str] = set()
            for doc in self.loader.load_all():
                seen.add(doc.doc_id)
                if stored.get(doc.doc_id) != doc.fingerprint:
                    self._index_document(doc)
            # Delete documents that are no longer present in the source.
            for doc_id in set(stored) - seen:
                self.store.delete_document(doc_id)
            with self._lock:
                self._last_indexed_at = time.time()
                self._last_error = None
        except Exception as exc:
            logger.exception("reindex failed for source '%s'", self.name)
            with self._lock:
                self._last_error = str(exc)
            raise
        finally:
            with self._lock:
                self._indexing = False
        return self.status()

    def reindex_document(self, doc_id: str) -> None:
        """Reconcile a single document: add/update if present, else delete.

        Used by watchers reacting to a single file/page change.
        """
        with self._lock:
            self._indexing = True
        try:
            doc = self.loader.load_one(doc_id)
            if doc is None:
                self.store.delete_document(doc_id)
            else:
                self._index_document(doc)
            with self._lock:
                self._last_indexed_at = time.time()
                self._last_error = None
        except Exception as exc:
            logger.exception(
                "reindex of document '%s' failed for source '%s'", doc_id, self.name
            )
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._indexing = False

    def _index_document(self, doc: RawDocument) -> None:
        """Chunk, embed, and upsert one document (replacing its old chunks)."""
        chunks = chunk_text(self.config.chunking, doc.content)
        if not chunks:
            # Empty/blank document: ensure no stale chunks linger.
            self.store.delete_document(doc.doc_id)
            return
        embeddings = self.embedder.embed(chunks)
        metadatas = [
            self._chunk_metadata(doc, index) for index in range(len(chunks))
        ]
        self.store.upsert_chunks(
            doc_id=doc.doc_id,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )

    def _chunk_metadata(self, doc: RawDocument, chunk_index: int) -> dict:
        """Compose the metadata stamped on a chunk.

        Order matters: static source metadata first, then per-document metadata,
        then the bookkeeping fields the pipeline relies on (so those can't be
        accidentally overridden by source config).
        """
        meta: dict = {}
        meta.update(self.config.metadata)
        meta.update(doc.metadata)
        meta.update(
            {
                "source": self.name,
                "doc_id": doc.doc_id,
                "chunk_index": chunk_index,
                "fingerprint": doc.fingerprint,
            }
        )
        return meta

    # -- query --------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter: Optional[dict] = None,
    ) -> list[dict]:
        """Semantic search within this source. Returns SearchResult dicts."""
        embedding = self.embedder.embed_query(query)
        hits = self.store.query(embedding, top_k=top_k, where=filter)
        results = []
        for hit in hits:
            results.append(
                {
                    "source": self.name,
                    "doc_id": hit.metadata.get("doc_id"),
                    "content": hit.document,
                    "metadata": hit.metadata,
                    "score": hit.score,
                }
            )
        return results

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Fetch a full document by id, or ``None`` if unknown."""
        doc = self.loader.load_one(doc_id)
        if doc is None:
            return None
        metadata = {**self.config.metadata, **doc.metadata}
        return {
            "source": self.name,
            "doc_id": doc.doc_id,
            "content": doc.content,
            "metadata": metadata,
        }

    # -- status -------------------------------------------------------------

    def status(self) -> dict:
        """IndexStatus dict for get_index_status / reindex."""
        with self._lock:
            indexing = self._indexing
            last_indexed_at = self._last_indexed_at
            last_error = self._last_error
        return {
            "name": self.name,
            "indexing": indexing,
            "document_count": self.store.document_count(),
            "chunk_count": self.store.chunk_count(),
            "last_indexed_at": last_indexed_at,
            "last_error": last_error,
        }

    def source_info(self) -> dict:
        """SourceInfo dict for list_sources."""
        status = self.status()
        return {
            "name": self.name,
            "type": self.config.type,
            "description": self.config.description,
            "collection": self.config.vector_store.collection,
            "document_count": status["document_count"],
            "chunk_count": status["chunk_count"],
            "last_indexed_at": status["last_indexed_at"],
            "last_error": status["last_error"],
            "watch_enabled": self.config.watch.enabled,
            "watch_mode": self.config.watch.mode,
            "metadata": self.config.metadata,
        }
