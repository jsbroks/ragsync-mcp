"""SourcePipeline incremental-indexing and search tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragsync_mcp.config import AppConfig
from ragsync_mcp.ingestion import SourcePipeline


def _make_pipeline(tmp_path: Path, docs: Path, *, metadata=None) -> SourcePipeline:
    cfg = AppConfig(
        defaults={"vector_store": {"persist_directory": str(tmp_path / "db")}},
        sources=[
            {
                "name": "docs",
                "type": "folder",
                "connection": {"path": str(docs), "include": ["**/*.md"]},
                "vector_store": {"collection": "docs"},
                "metadata": metadata or {},
            }
        ],
    )
    return SourcePipeline(cfg.sources[0])


@pytest.fixture()
def docs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "alpha.md").write_text("# Alpha\n\nThe quick brown fox jumps the lazy dog.")
    (d / "beta.md").write_text("# Beta\n\nCamera calibration alignment wizard guide.")
    return d


def test_initial_index_counts(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    status = pipe.status()
    assert status["document_count"] == 2
    assert status["chunk_count"] >= 2
    assert status["last_error"] is None


def test_search_returns_relevant_doc_first(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    results = pipe.search("camera calibration wizard", top_k=2)
    assert results
    assert results[0]["doc_id"] == "beta.md"
    assert 0.0 <= results[0]["score"] <= 1.0


def test_unchanged_docs_are_skipped(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    first = pipe.store.fingerprint_map()
    pipe.full_reindex()  # nothing changed
    assert pipe.store.fingerprint_map() == first


def test_changed_doc_is_reindexed(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    (docs_dir / "alpha.md").write_text("# Alpha\n\nCompletely new content about networking.")
    pipe.full_reindex()
    results = pipe.search("networking", top_k=1)
    assert results and results[0]["doc_id"] == "alpha.md"


def test_deleted_doc_is_removed(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    (docs_dir / "beta.md").unlink()
    pipe.full_reindex()
    assert pipe.status()["document_count"] == 1
    assert all(r["doc_id"] != "beta.md" for r in pipe.search("camera", top_k=5))


def test_reindex_document_add_and_delete(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    # Add a new file and reconcile just it (the watcher's code path).
    (docs_dir / "gamma.md").write_text("# Gamma\n\nFresh troubleshooting playbook steps.")
    pipe.reindex_document("gamma.md")
    assert pipe.status()["document_count"] == 3
    # Delete it on disk and reconcile just it.
    (docs_dir / "gamma.md").unlink()
    pipe.reindex_document("gamma.md")
    assert pipe.status()["document_count"] == 2


def test_get_document_returns_full_content(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    doc = pipe.get_document("alpha.md")
    assert doc is not None
    assert "quick brown fox" in doc["content"]
    assert doc["metadata"]["rel_path"] == "alpha.md"


def test_get_document_unknown_returns_none(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir)
    pipe.full_reindex()
    assert pipe.get_document("nope.md") is None


def test_static_metadata_filter(tmp_path, docs_dir):
    pipe = _make_pipeline(tmp_path, docs_dir, metadata={"audience": "public"})
    pipe.full_reindex()
    hits = pipe.search("fox", top_k=5, filter={"audience": "public"})
    assert hits
    miss = pipe.search("fox", top_k=5, filter={"audience": "secret"})
    assert miss == []
