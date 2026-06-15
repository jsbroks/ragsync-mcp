"""Chroma persistent-client wrapper.

One ``ChromaStore`` wraps one collection. The pipeline supplies pre-computed
embeddings, so Chroma is used as a pure vector index (no embedding function of
its own) — that keeps the embedding model the single source of truth and lets
each source use a different model safely.

Conventions enforced here:

  - Cosine space (``hnsw:space=cosine``) so distances are comparable across
    sources and normalizable to a [0, 1] similarity at the tool boundary.
  - Chunk ids are deterministic ``f"{doc_id}::{chunk_index}"``; ``upsert_chunks``
    deletes a document's existing chunks before adding new ones, so updates
    leave no orphans when a document shrinks.
  - Per-chunk metadata carries ``doc_id`` and ``fingerprint`` so the pipeline
    can reconstruct the fingerprint map for incremental indexing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import chromadb
from chromadb.config import Settings


@dataclass
class QueryHit:
    """One search hit from the store, with a normalized [0, 1] score."""

    chunk_id: str
    document: str
    metadata: dict
    score: float


def _normalize_cosine_distance(distance: float) -> float:
    """Map a Chroma cosine distance (0..2) to a similarity in [0, 1].

    distance 0 -> 1.0 (identical direction); distance 2 -> 0.0 (opposite).
    Clamped so numerical noise can't push the score outside [0, 1].
    """
    score = 1.0 - (distance / 2.0)
    return max(0.0, min(1.0, score))


class ChromaStore:
    """A thin wrapper over a single Chroma collection."""

    def __init__(
        self,
        persist_directory: str,
        collection: str,
        embedding_signature: str,
    ):
        self._client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        # Stamp the embedding signature on the collection so the operator can
        # spot an accidental model swap (config.py already prevents two
        # differently-embedded sources from sharing a collection name).
        self._collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine", "embedding_signature": embedding_signature},
        )

    # -- writes -------------------------------------------------------------

    def upsert_chunks(
        self,
        doc_id: str,
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        """Replace all chunks for ``doc_id`` with the supplied ones.

        Deletes existing chunks first so a shrinking document leaves no orphan
        chunks behind. A document that produced no chunks is simply cleared.
        """
        self.delete_document(doc_id)
        if not documents:
            return
        ids = [f"{doc_id}::{i}" for i in range(len(documents))]
        self._collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def delete_document(self, doc_id: str) -> None:
        """Remove every chunk belonging to ``doc_id``."""
        self._collection.delete(where={"doc_id": doc_id})

    # -- reads --------------------------------------------------------------

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: Optional[dict] = None,
    ) -> list[QueryHit]:
        """Nearest-neighbour search, returning hits with normalized scores."""
        if top_k <= 0:
            return []
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=_build_where(where),
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        hits: list[QueryHit] = []
        for chunk_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            hits.append(
                QueryHit(
                    chunk_id=chunk_id,
                    document=doc,
                    metadata=dict(meta or {}),
                    score=_normalize_cosine_distance(dist),
                )
            )
        return hits

    def fingerprint_map(self) -> dict[str, str]:
        """Map each stored ``doc_id`` to its recorded fingerprint.

        Drives incremental indexing: the pipeline compares this against a fresh
        scan to decide what to (re)index or delete.
        """
        result = self._collection.get(include=["metadatas"])
        mapping: dict[str, str] = {}
        for meta in result.get("metadatas") or []:
            if not meta:
                continue
            doc_id = meta.get("doc_id")
            if doc_id is not None:
                mapping[doc_id] = meta.get("fingerprint", "")
        return mapping

    def document_count(self) -> int:
        """Number of distinct documents currently indexed."""
        return len(self.fingerprint_map())

    def chunk_count(self) -> int:
        """Total number of chunks in the collection."""
        return self._collection.count()


def _build_where(where: Optional[dict]) -> Optional[dict]:
    """Translate a flat equality filter into Chroma's ``where`` syntax.

    A single key passes through as ``{k: v}``; multiple keys are combined with
    ``$and`` (Chroma rejects multi-key top-level filters otherwise). An empty or
    missing filter returns ``None`` (no filtering).
    """
    if not where:
        return None
    clauses = [{key: value} for key, value in where.items()]
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
