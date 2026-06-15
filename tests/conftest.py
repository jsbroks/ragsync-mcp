"""Shared test fixtures.

Tests must run offline, so we inject a deterministic, dependency-free embedder
in place of fastembed by replacing the registry entry in ``embeddings._PROVIDERS``
(the injection point documented in AGENTS.md). The embedder is a hashing
bag-of-words vectorizer with L2 normalization, so cosine similarity reflects
token overlap — enough for the relevance ordering the pipeline tests assert.
"""

from __future__ import annotations

import hashlib
import math
import re

import pytest

from ragsync_mcp import embeddings

_DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")


class DeterministicEmbedder:
    """Stable hashing embedder usable without any network or model download."""

    def __init__(self, config) -> None:
        self.signature = f"test:{config.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        vec = [0.0] * _DIM
        for token in _TOKEN.findall(text.lower()):
            idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % _DIM
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            # Avoid a zero vector (undefined cosine); nudge one component.
            vec[0] = 1.0
            norm = 1.0
        return [x / norm for x in vec]


@pytest.fixture(autouse=True)
def deterministic_embedder():
    """Swap the fastembed provider for the deterministic one for every test."""
    original = embeddings._PROVIDERS.get("fastembed")
    embeddings._PROVIDERS["fastembed"] = DeterministicEmbedder
    try:
        yield
    finally:
        if original is not None:
            embeddings._PROVIDERS["fastembed"] = original
