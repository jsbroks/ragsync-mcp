"""Embedding providers behind a registry.

An ``Embedder`` turns text into vectors. Which provider/model to use comes from
a source's ``EmbeddingConfig``; the pipeline only ever talks to the ``Embedder``
interface, never a specific SDK.

Providers are registered in ``_PROVIDERS`` keyed by ``EmbeddingConfig.provider``.
Tests inject a deterministic embedder by replacing an entry in ``_PROVIDERS``
before ``server.initialize(...)`` — see AGENTS.md — rather than editing this
module.

The default provider, ``fastembed``, runs fully locally (ONNX, CPU) and needs no
API key, so the server works out of the box. ``openai`` and ``voyage`` are
hosted and read their key from the env var named by ``api_key_env``.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol

from .config import EmbeddingConfig


class Embedder(Protocol):
    """The interface the pipeline depends on.

    ``signature`` identifies the embedding space (provider:model). It is stored
    on the collection so a mismatched re-use is caught. ``embed`` vectorizes a
    batch of documents; ``embed_query`` vectorizes one query string (some
    providers embed queries and passages differently).
    """

    signature: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _to_list(vector) -> list[float]:
    """Coerce a numpy array / sequence into a plain list of floats."""
    tolist = getattr(vector, "tolist", None)
    if tolist is not None:
        return tolist()
    return [float(x) for x in vector]


class FastEmbedEmbedder:
    """Local ONNX embeddings via the ``fastembed`` package."""

    def __init__(self, config: EmbeddingConfig):
        from fastembed import TextEmbedding

        self.signature = config.signature()
        self._model = TextEmbedding(model_name=config.model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_to_list(v) for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        # fastembed exposes query_embed for asymmetric retrieval models; fall
        # back to plain embed for models that don't distinguish.
        query_embed = getattr(self._model, "query_embed", None)
        if query_embed is not None:
            return _to_list(next(iter(query_embed(text))))
        return _to_list(next(iter(self._model.embed([text]))))


class OpenAIEmbedder:
    """Hosted OpenAI embeddings. Key read from the env var named in config."""

    def __init__(self, config: EmbeddingConfig):
        from openai import OpenAI

        api_key = _require_key(config)
        self.signature = config.signature()
        self._model = config.model
        self._client = OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in resp.data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class VoyageEmbedder:
    """Hosted Voyage AI embeddings. Key read from the env var named in config."""

    def __init__(self, config: EmbeddingConfig):
        import voyageai

        api_key = _require_key(config)
        self.signature = config.signature()
        self._model = config.model
        self._client = voyageai.Client(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed(texts, model=self._model, input_type="document").embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed([text], model=self._model, input_type="query").embeddings[0]


def _require_key(config: EmbeddingConfig) -> str:
    """Read the API key from the configured env var, or fail loudly."""
    env_name = config.api_key_env
    if not env_name:
        raise ValueError(
            f"embedding provider '{config.provider}' requires api_key_env"
        )
    key = os.environ.get(env_name)
    if not key:
        raise ValueError(
            f"environment variable '{env_name}' is not set "
            f"(needed for embedding provider '{config.provider}')"
        )
    return key


# Provider registry. Map provider name -> factory taking an EmbeddingConfig.
# Tests replace entries here to inject deterministic embedders offline.
_PROVIDERS: dict[str, Callable[[EmbeddingConfig], Embedder]] = {
    "fastembed": FastEmbedEmbedder,
    "openai": OpenAIEmbedder,
    "voyage": VoyageEmbedder,
}


def get_embedder(config: EmbeddingConfig) -> Embedder:
    """Build the embedder for a source's embedding config."""
    factory = _PROVIDERS.get(config.provider)
    if factory is None:
        raise ValueError(
            f"unknown embedding provider '{config.provider}'. "
            f"Known providers: {sorted(_PROVIDERS)}"
        )
    return factory(config)
