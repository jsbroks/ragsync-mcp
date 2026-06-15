"""Text chunking strategies.

A chunker turns one document's text into a list of overlapping chunks sized for
embedding. Strategy and sizing come from a source's ``ChunkingConfig`` so the
pipeline never hard-codes splitting rules.

The actual splitting is delegated to ``langchain-text-splitters`` rather than a
hand-rolled splitter. Two strategies are supported (matching ``ChunkingConfig``):

  - ``recursive_character`` — ``RecursiveCharacterTextSplitter``: split on a
    hierarchy of separators (paragraph, line, sentence, word) to keep chunks
    near ``chunk_size`` characters while preferring clean boundaries.
  - ``markdown`` — ``MarkdownTextSplitter``: the recursive splitter pre-seeded
    with Markdown-aware separators (headings, code fences, lists) so chunks
    respect document structure.
"""

from __future__ import annotations

from langchain_text_splitters import (
    MarkdownTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .config import ChunkingConfig


def chunk_text(config: ChunkingConfig, text: str) -> list[str]:
    """Split ``text`` into chunks according to ``config``.

    Returns an empty list for blank/whitespace-only input.
    """
    if not text or not text.strip():
        return []
    splitter = _build_splitter(config)
    return [c for c in splitter.split_text(text) if c.strip()]


def _build_splitter(config: ChunkingConfig):
    """Construct the langchain splitter for the configured strategy."""
    kwargs = dict(chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
    if config.strategy == "markdown":
        return MarkdownTextSplitter(**kwargs)
    return RecursiveCharacterTextSplitter(**kwargs)
