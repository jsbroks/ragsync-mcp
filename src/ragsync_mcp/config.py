"""Configuration schema for the generic RAG MCP server.

A single YAML file drives the whole server. Each "source" describes:
  - where data comes from (type + typed connection details)
  - how/whether it should be watched for changes
  - how it should be chunked
  - which embedding provider/model to use
  - which vector store collection to write into
  - a natural-language description (so an agent can route to the right source)
  - static metadata stamped onto every chunk (for filtering/citation)

A top-level `defaults` block supplies chunking/embedding/vector_store settings
that each source inherits unless it overrides them.

Design notes:
  - Every model sets `extra="forbid"` so a typo'd YAML key is a loud error
    rather than a silently ignored field.
  - Connection blocks are validated up front (discriminated by source `type`)
    instead of being passed around as untyped dicts.
  - Field descriptions flow into the JSON Schema (model_json_schema()) for
    editor autocomplete and LLM-assisted config authoring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# A frozen base so typos in YAML keys are rejected and configs are immutable
# once validated (the pipeline relies on config not changing under it).
_DEFAULT_PERSIST_DIR = "./vector_db"


def _resolve_path(raw: str, base_dir: Path) -> str:
    """Resolve ``raw`` to an absolute path.

    Absolute and ``~``-prefixed paths are honoured as-is; a relative path is
    anchored to ``base_dir`` (the directory holding the config file).
    """
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((base_dir / expanded).resolve())


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Sub-configs (chunking / embedding / vector store / watch)
# ---------------------------------------------------------------------------


class WatchConfig(_Base):
    enabled: bool = Field(
        default=False,
        description="Whether to watch this source for changes after the initial index.",
    )
    mode: Literal["filesystem", "poll"] = Field(
        default="poll",
        description=(
            "'filesystem' uses native OS file events (folder sources only); "
            "'poll' re-scans on an interval (any source type)."
        ),
    )
    interval_seconds: int = Field(
        default=300,
        ge=5,
        description="Poll interval in seconds. Only used when mode='poll'.",
    )
    recursive: bool = Field(
        default=True,
        description="Watch subdirectories recursively (folder/filesystem only).",
    )


class ChunkingConfig(_Base):
    strategy: Literal["recursive_character", "markdown"] = Field(
        default="recursive_character",
        description="Text splitting strategy. Use 'markdown' for .md-heavy sources.",
    )
    chunk_size: int = Field(
        default=800,
        gt=0,
        le=8000,
        description="Target chunk size in characters.",
    )
    chunk_overlap: int = Field(
        default=100,
        ge=0,
        description="Characters of overlap between adjacent chunks.",
    )

    @model_validator(mode="after")
    def _overlap_below_size(self) -> "ChunkingConfig":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})."
            )
        return self


class EmbeddingConfig(_Base):
    provider: Literal["fastembed", "openai", "voyage"] = Field(
        default="fastembed",
        description="Embedding provider. 'fastembed' runs locally with no API key.",
    )
    model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Embedding model name, as understood by the chosen provider.",
    )
    api_key_env: Optional[str] = Field(
        default=None,
        description=(
            "Name of the environment variable holding the API key "
            "(never the key itself). Required for 'openai' and 'voyage'."
        ),
    )

    @model_validator(mode="after")
    def _hosted_requires_key_env(self) -> "EmbeddingConfig":
        if self.provider in ("openai", "voyage") and not self.api_key_env:
            raise ValueError(
                f"embedding.provider '{self.provider}' requires 'api_key_env' "
                f"(the name of the env var holding the API key)."
            )
        return self

    def signature(self) -> str:
        """Identity used to detect incompatible collections sharing a name.

        Two sources writing to the same collection must share this signature,
        otherwise their vectors live in different embedding spaces and search
        is corrupted.
        """
        return f"{self.provider}:{self.model}"


class VectorStoreConfig(_Base):
    backend: Literal["chroma"] = Field(
        default="chroma",
        description="Vector store backend.",
    )
    collection: Optional[str] = Field(
        default=None,
        description="Collection name. Required per-source; omit in `defaults`.",
    )
    persist_directory: str = Field(
        default=_DEFAULT_PERSIST_DIR,
        description=(
            "On-disk directory for the vector store. A relative path is "
            "resolved against the directory containing the config file."
        ),
    )

    @field_validator("collection")
    @classmethod
    def _collection_charset(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v or any(c.isspace() for c in v) or any(c in v for c in "/\\'\""):
            raise ValueError(
                "collection must be non-empty and contain no whitespace, "
                "slashes, or quotes."
            )
        return v


# ---------------------------------------------------------------------------
# Defaults block (everything optional; supplies inherited values)
# ---------------------------------------------------------------------------


class VectorStoreDefaults(_Base):
    """Vector store defaults: like VectorStoreConfig but never carries a collection."""

    backend: Literal["chroma"] = "chroma"
    persist_directory: str = _DEFAULT_PERSIST_DIR


class Defaults(_Base):
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreDefaults = Field(default_factory=VectorStoreDefaults)


# ---------------------------------------------------------------------------
# Typed connection blocks (discriminated by `type` on the parent SourceConfig)
# ---------------------------------------------------------------------------


class FolderConnection(_Base):
    path: str = Field(
        description=(
            "Path to the source folder. May be absolute, ~-expanded, or relative "
            "to the directory containing the config file."
        )
    )
    include: list[str] = Field(
        default_factory=lambda: ["**/*"],
        description="Glob patterns (relative to path) to include.",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Glob patterns to exclude.",
    )


class WebsiteConnection(_Base):
    urls: list[str] = Field(
        min_length=1,
        description="Explicit list of page URLs to fetch (not crawled).",
    )

    @field_validator("urls")
    @classmethod
    def _urls_are_http(cls, v: list[str]) -> list[str]:
        for url in v:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"URL must start with http:// or https://: {url!r}")
        return v


# Registry used by loaders and by SourceConfig validation.
_CONNECTION_MODELS: dict[str, type[_Base]] = {
    "folder": FolderConnection,
    "website": WebsiteConnection,
}

ConnectionUnion = Union[FolderConnection, WebsiteConnection]


# ---------------------------------------------------------------------------
# Source config
# ---------------------------------------------------------------------------


class SourceConfig(_Base):
    name: str = Field(description="Unique source identifier; used as the `source` arg in search.")
    type: Literal["folder", "website"] = Field(description="Source type (selects the loader).")
    connection: ConnectionUnion = Field(
        description="Connection details; schema depends on `type`.",
    )
    description: str = Field(
        default="",
        description="Natural-language description of what this source contains "
        "and when to use it (surfaced to the agent via list_sources).",
    )
    watch: WatchConfig = Field(default_factory=WatchConfig)
    chunking: Optional[ChunkingConfig] = Field(
        default=None, description="Overrides defaults.chunking when set."
    )
    embedding: Optional[EmbeddingConfig] = Field(
        default=None, description="Overrides defaults.embedding when set."
    )
    vector_store: VectorStoreConfig = Field(
        default_factory=VectorStoreConfig,
        description="Vector store settings; `collection` is required.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Static key/value metadata stamped onto every chunk.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_connection(cls, data):
        """Parse the raw connection dict into the right typed model for `type`.

        Runs before field validation so an unknown type or a malformed
        connection block fails here with a clear message instead of being
        accepted as an opaque dict.
        """
        if not isinstance(data, dict):
            return data
        src_type = data.get("type")
        conn = data.get("connection")
        if src_type is not None and isinstance(conn, dict):
            model = _CONNECTION_MODELS.get(src_type)
            if model is None:
                raise ValueError(
                    f"Unknown source type {src_type!r}. "
                    f"Known types: {sorted(_CONNECTION_MODELS)}"
                )
            data = {**data, "connection": model(**conn)}
        return data

    def get_connection(self) -> ConnectionUnion:
        """Return the already-parsed, typed connection model."""
        return self.connection


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class AppConfig(_Base):
    defaults: Defaults = Field(default_factory=Defaults)
    sources: list[SourceConfig] = Field(
        min_length=1, description="At least one knowledge source."
    )

    @model_validator(mode="after")
    def _apply_defaults_and_validate(self) -> "AppConfig":
        seen_names: set[str] = set()
        collection_signatures: dict[str, str] = {}

        for source in self.sources:
            # Inherit unset blocks from defaults (deep-copied so sources don't
            # share mutable sub-objects with each other or with defaults).
            if source.chunking is None:
                source.chunking = self.defaults.chunking.model_copy(deep=True)
            if source.embedding is None:
                source.embedding = self.defaults.embedding.model_copy(deep=True)

            # Vector store: collection is per-source; backend/persist_dir inherit
            # from defaults only when the source left them at their own defaults.
            vs = source.vector_store
            if vs.persist_directory == _DEFAULT_PERSIST_DIR:
                vs.persist_directory = self.defaults.vector_store.persist_directory
            # backend currently has a single value; inherit defensively.
            vs.backend = vs.backend or self.defaults.vector_store.backend

            # Unique source names.
            if source.name in seen_names:
                raise ValueError(f"Duplicate source name '{source.name}'")
            seen_names.add(source.name)

            # Collection required on every source.
            if not vs.collection:
                raise ValueError(
                    f"Source '{source.name}' must set vector_store.collection"
                )

            # No two differently-embedded sources may share a collection.
            sig = source.embedding.signature()
            existing = collection_signatures.get(vs.collection)
            if existing is not None and existing != sig:
                raise ValueError(
                    f"Collection '{vs.collection}' is used by sources with different "
                    f"embedding configs ({existing} vs {sig}); this corrupts search. "
                    f"Use a distinct collection per embedding configuration."
                )
            collection_signatures[vs.collection] = sig

        return self

    @classmethod
    def from_yaml(cls, path: str) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = cls(**data)
        # Resolve relative filesystem paths against the config file's directory,
        # so a config can live in the repo and reference repo content portably
        # regardless of the process working directory.
        config._resolve_relative_paths(Path(path).resolve().parent)
        return config

    def _resolve_relative_paths(self, base_dir: Path) -> None:
        """Anchor relative folder/persist paths to ``base_dir`` (in place)."""
        for source in self.sources:
            vs = source.vector_store
            vs.persist_directory = _resolve_path(vs.persist_directory, base_dir)
            conn = source.connection
            if isinstance(conn, FolderConnection):
                conn.path = _resolve_path(conn.path, base_dir)

    @classmethod
    def json_schema(cls) -> dict:
        """JSON Schema for the config, for editor autocomplete / LLM authoring."""
        return cls.model_json_schema()