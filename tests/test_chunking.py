"""Chunking behaviour tests."""

from __future__ import annotations

from ragsync_mcp.chunking import chunk_text
from ragsync_mcp.config import ChunkingConfig


def test_blank_text_yields_no_chunks():
    assert chunk_text(ChunkingConfig(), "   \n  ") == []


def test_short_text_is_single_chunk():
    chunks = chunk_text(ChunkingConfig(chunk_size=800), "a short doc")
    assert chunks == ["a short doc"]


def test_long_text_is_split_with_overlap():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(20))
    chunks = chunk_text(ChunkingConfig(chunk_size=300, chunk_overlap=50), text)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)  # near target, allowing boundaries


def test_markdown_strategy_runs():
    md = "# Title\n\nIntro.\n\n## Section\n\n" + ("detail " * 100)
    chunks = chunk_text(
        ChunkingConfig(strategy="markdown", chunk_size=200, chunk_overlap=20), md
    )
    assert len(chunks) >= 2
