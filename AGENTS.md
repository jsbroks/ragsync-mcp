# AGENTS.md

Guidance for AI coding agents (and humans) working on this repository. Read this
before making changes.

## What this project is

A configuration-driven RAG MCP server. A single YAML file defines one or more
knowledge **sources**; the server ingests, watches, chunks, embeds, and indexes
them, then exposes a small fixed set of MCP tools for semantic search and
retrieval. The design invariant: **everything that varies between deployments
lives in YAML; the MCP tool surface never changes.** Do not add tools or change
tool signatures to accommodate a new source type — extend the registries instead.

This is a standalone, open-source service. Keep it generic — it is not tied to
any particular consumer or product. Don't hard-code names, paths, or assumptions
from any downstream user into the code or docs.

See `DESIGN.md` for the full specification and `README.md` for usage.

## Architecture (and where to make changes)

```
src/ragsync_mcp/
  config.py        YAML schema (Pydantic), defaults inheritance, validation
  config_watch.py  watches the config file itself for live reload
  loaders/         source readers behind a registry; return RawDocument
  chunking.py      text splitters (recursive_character, markdown) via langchain
  embeddings.py    embedding providers behind a registry (fastembed/openai/voyage)
  vectorstore.py   Chroma persistent-client wrapper
  ingestion.py     per-source pipeline: load -> chunk -> embed -> upsert; fingerprints
  watchers/        change watchers behind a registry (filesystem, polling)
  server.py        MCP tool layer + startup/initialization + config hot-reload
```

Data flows in one direction: **loader -> chunker -> embedder -> vector store**,
orchestrated per-source by `SourcePipeline` in `ingestion.py`. The MCP tools in
`server.py` only ever read through `SourcePipeline`; they never touch loaders or
the vector store directly.

## The extension contract (most common task)

When asked to "support a new source type" (Confluence, Notion, S3, etc.):

1. Add a connection schema in `config.py` and register it in `_CONNECTION_MODELS`.
2. Implement a loader in `loaders/` subclassing `BaseLoader`, implementing
   `load_all()` and `load_one(doc_id)`, returning `RawDocument` objects.
3. Register the loader in `loaders/__init__.py` (`LOADER_REGISTRY`).
4. If it needs a new watch strategy, add a watcher in `watchers/` and register it.

Do NOT modify chunking, embeddings, vectorstore, ingestion, or server.py for a
new source — if you find yourself needing to, the abstraction is leaking and the
change should be reconsidered.

### RawDocument invariants (critical — do not break)

Every loader must produce `RawDocument(doc_id, content, metadata, fingerprint)`:

- `doc_id` must be **unique within the source** and **stable across re-indexes**
  (the same document yields the same id every time). It must also be
  **reversible**: `load_one(doc_id)` must be able to fetch exactly that document.
  Folder loader uses the path relative to root; website loader uses the URL.
  For a new source, pick the natural stable id (Confluence page id, S3 key, etc.).
- `fingerprint` must change iff the content changes (use a content hash). This
  drives incremental indexing — unchanged docs are skipped, changed docs are
  delete-then-upserted. Getting this wrong causes stale or duplicated chunks.
- `metadata` is stamped onto every chunk; keep it JSON-serializable and small.

## Embedding / collection invariant (critical)

Chunks for one collection must all be produced by the **same embedding model**.
Mixing embedding dimensions/spaces in a collection silently corrupts search.
`config.py` validates that no two sources with different embedding configs share
a `vector_store.collection` — preserve this check. New collections get a new
embedding signature; do not bypass the validator.

## Update / delete semantics (don't reinvent)

Already implemented in `ingestion.py` + `vectorstore.py`:

- Chunk ids are deterministic: `f"{doc_id}::{chunk_index}"`.
- `upsert_chunks` deletes all existing chunks for a `doc_id` before adding new
  ones (clean updates, no orphans when a doc shrinks).
- `full_reindex` diffs the current scan against the fingerprint map and deletes
  docs that disappeared. `reindex_document` handles single-file add/change/delete.
  Reuse these paths; don't add parallel deletion logic.

## Conventions

- Tools return structured `{"error": "..."}` dicts rather than raising, so the
  calling agent can recover conversationally. Keep this pattern for new tools.
- `search` returns normalized scores in [0, 1] (1 = most similar) at the tool
  boundary, regardless of the store's native distance metric. New retrieval
  tools must normalize the same way.
- Secrets are referenced by **env var name** in config (`api_key_env`), never as
  literal values. Never read or write raw secrets into config or logs.
- Per-source isolation is a security boundary (see "Access scoping" in README):
  access is scoped by running separate server instances with separate configs.
  Do not add a cross-source "search everything regardless of instance" path.
- Python 3.10+. Type-hint new code. Keep modules small and single-purpose.

## Build / run / test

```bash
uv sync                                # core deps
uv sync --extra openai                 # optional hosted embedding provider
uv sync --extra dev                    # test deps (pytest)
uv run ragsync-mcp --config examples/config.example.yaml
```

There is no network access to model hosts in some sandboxes; `fastembed`
downloads weights on first run. When testing offline, inject a deterministic
embedder by replacing entries in `embeddings._PROVIDERS` before
`server.initialize(...)` rather than editing production code.

When you change indexing/search logic, verify end-to-end against
`examples/config.example.yaml`: confirm (1) both sources index, (2) unscoped search ranks
the relevant source first, (3) scoped search restricts correctly, (4) a metadata
`filter` narrows results, (5) adding/removing a file under a watched folder
updates the index. Delete `vector_db*/` between runs for a clean slate.

## Things to avoid

- Don't add MCP tools casually. Five tools (`search`, `list_sources`,
  `get_document`, `get_index_status`, `reindex`) cover the contract. New tools
  need a clear, distinct purpose (e.g. `read_chunk_neighbors`) and a docstring
  that tells the calling agent exactly when to use them.
- Don't make `search` auto-fetch neighbors or whole documents. Retrieval breadth
  is the calling agent's decision via separate tools; keep `search` cheap.
- Don't persist absolute machine paths as `doc_id` (breaks portability/citation).
- Don't introduce a second language or a heavy service for what a loader can do.
