"""Filesystem watcher integration test.

Timing-sensitive (real OS events + debounce), so it polls for the expected
state with a generous timeout rather than sleeping a fixed amount.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ragsync_mcp.config import AppConfig
from ragsync_mcp.ingestion import SourcePipeline
from ragsync_mcp.watchers import build_watcher


def _wait_for(predicate, timeout=15.0, interval=0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _watched_pipeline(tmp_path: Path, docs: Path) -> SourcePipeline:
    cfg = AppConfig(
        defaults={"vector_store": {"persist_directory": str(tmp_path / "db")}},
        sources=[
            {
                "name": "docs",
                "type": "folder",
                "connection": {"path": str(docs), "include": ["**/*.md"]},
                "watch": {"enabled": True, "mode": "filesystem"},
                "vector_store": {"collection": "docs"},
            }
        ],
    )
    return SourcePipeline(cfg.sources[0])


def test_filesystem_watch_picks_up_add_and_delete(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "one.md").write_text("# One\n\nFirst document content.")

    pipe = _watched_pipeline(tmp_path, docs)
    pipe.full_reindex()
    assert pipe.status()["document_count"] == 1

    watcher = build_watcher(pipe)
    assert watcher is not None
    watcher.start()
    try:
        (docs / "two.md").write_text("# Two\n\nSecond document about calibration.")
        assert _wait_for(lambda: pipe.status()["document_count"] == 2), "add not detected"

        (docs / "two.md").unlink()
        assert _wait_for(lambda: pipe.status()["document_count"] == 1), "delete not detected"
    finally:
        watcher.stop()
