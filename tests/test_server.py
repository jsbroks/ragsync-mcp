"""MCP tool-layer tests via server.initialize() + the tool functions.

Exercises the AGENTS.md end-to-end checklist across two sources: both index,
unscoped search ranks the relevant source first, scoped search restricts, a
metadata filter narrows, and the exclude glob keeps internal files out.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ragsync_mcp import server


@pytest.fixture()
def initialized(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "internal").mkdir(parents=True)
    (docs / "cameras.md").write_text(
        "# Cameras\n\nCamera calibration and lens alignment wizard instructions."
    )
    (docs / "internal" / "secret.md").write_text("# Secret\n\nInternal roadmap, do not index.")

    playbooks = tmp_path / "playbooks"
    playbooks.mkdir()
    (playbooks / "scan.md").write_text(
        "# Scan Fails\n\nTroubleshooting playbook: free disk space and reseat the cable."
    )

    config = tmp_path / "config.yaml"
    config.write_text(
        textwrap.dedent(
            f"""
            defaults:
              vector_store:
                persist_directory: {tmp_path / "db"}
            sources:
              - name: product-docs
                type: folder
                connection:
                  path: {docs}
                  include: ["**/*.md"]
                  exclude: ["**/internal/**"]
                vector_store:
                  collection: product_docs
                metadata:
                  audience: public
              - name: playbooks
                type: folder
                connection:
                  path: {playbooks}
                  include: ["**/*.md"]
                vector_store:
                  collection: playbooks
                metadata:
                  audience: support
            """
        )
    )

    server.initialize(str(config), watch_config=False)
    try:
        yield
    finally:
        server.shutdown()


def test_list_sources(initialized):
    infos = {s["name"]: s for s in server.list_sources()}
    assert set(infos) == {"product-docs", "playbooks"}
    assert infos["product-docs"]["document_count"] == 1  # internal/ excluded
    assert infos["product-docs"]["metadata"] == {"audience": "public"}


def test_exclude_glob_keeps_internal_out(initialized):
    results = server.search("internal roadmap secret")
    assert all("secret.md" not in (r.get("doc_id") or "") for r in results)


def test_unscoped_search_ranks_relevant_source_first(initialized):
    results = server.search("camera calibration wizard")
    assert results
    assert results[0]["source"] == "product-docs"


def test_scoped_search_restricts(initialized):
    results = server.search("disk space cable", source="playbooks")
    assert results
    assert all(r["source"] == "playbooks" for r in results if "score" in r)


def test_scoped_search_unknown_source_errors(initialized):
    results = server.search("anything", source="ghost")
    assert results and "error" in results[0]


def test_metadata_filter_narrows(initialized):
    public = server.search("calibration", filter={"audience": "public"})
    assert public and all(r["metadata"]["audience"] == "public" for r in public if "score" in r)
    none = server.search("calibration", filter={"audience": "nobody"})
    assert all("score" not in r for r in none)


def test_get_document_tool(initialized):
    doc = server.get_document("product-docs", "cameras.md")
    assert "lens alignment wizard" in doc["content"]
    err = server.get_document("product-docs", "missing.md")
    assert "error" in err


def test_reindex_tool(initialized):
    status = server.reindex("product-docs")
    assert status["name"] == "product-docs"
    assert status["last_error"] is None
    assert "error" in server.reindex("ghost")


def test_get_index_status_tool(initialized):
    all_status = server.get_index_status()
    assert len(all_status) == 2
    one = server.get_index_status("playbooks")
    assert one[0]["name"] == "playbooks"
