# Generic RAG MCP Server — Design Specification

A configuration-driven MCP server that ingests data from
arbitrary sources, watches them for changes, indexes them into vector stores,
and exposes a small, stable set of tools an LLM agent can call to search and
retrieve that knowledge.

The guiding principle: **everything that varies between deployments lives in a
YAML file; the tool surface the LLM sees never changes.** Adding a source,
swapping an embedding model, or pointing at a different vector store is a config
edit, not a code change.

---

## 1. Architecture at a glance

```
                         config.yaml
                             │
              ┌──────────────┴───────────────┐
              │      Config loader/validator   │
              └──────────────┬───────────────┘
                             │  one per source
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   SourcePipeline      SourcePipeline      SourcePipeline
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ loader   │        │ loader   │        │ loader   │
   │ chunker  │        │ chunker  │        │ chunker  │
   │ embedder │        │ embedder │        │ embedder │
   │ store    │        │ store    │        │ store    │
   │ watcher  │        │ watcher  │        │ watcher  │
   └──────────┘        └──────────┘        └──────────┘
         └───────────────────┼───────────────────┘
                             ▼
                   ┌──────────────────┐
                   │   MCP tool layer  │  ← stable, source-agnostic
                   └──────────────────┘
                             ▲
                             │  search / list_sources / get_document / ...
                          LLM agent
```

Each **source** is fully independent: its own loader, chunking rules, embedding
model, vector-store collection, and watcher. This isolation is what lets
different sources use different embedding models without corrupting each other's
similarity space — a hard requirement, since mixing embedding dimensions in one
collection silently breaks search.

---

## 2. YAML configuration schema

### 2.1 Top-level structure

```yaml
# Global defaults applied to every source unless the source overrides them.
defaults:
  chunking:
    strategy: recursive_character
    chunk_size: 800
    chunk_overlap: 100
  embedding:
    provider: fastembed
    model: BAAI/bge-small-en-v1.5
  vector_store:
    backend: chroma
    persist_directory: ./vector_db

# Named knowledge sources. Each becomes one searchable collection.
sources:
  - name: ...
    type: ...
    connection: { ... }
    watch: { ... }
    chunking: { ... } # optional; inherits from defaults
    embedding: { ... } # optional; inherits from defaults
    vector_store: { ... } # collection is required; rest inherits
    metadata: { ... } # optional static metadata stamped on every chunk
```

The `defaults` block is a convenience: a deployment with ten folder sources
that all share one embedding model writes the embedding config once. Anything
in a source block overrides the matching default key.

### 2.2 Per-source fields

#### `name` (string, required)

Unique identifier for the source. Used as the `source` argument in the `search`
tool and in result metadata. Must be unique across the config.

#### `type` (enum, required)

Which loader to use. Determines which `connection` schema is expected.
Supported values (extensible via the loader registry):

| type         | description                                    | watch modes          |
| ------------ | ---------------------------------------------- | -------------------- |
| `folder`     | local/mounted directory of files               | `filesystem`, `poll` |
| `website`    | fixed list of web pages (fetched, not crawled) | `poll`               |
| `confluence` | Confluence space (roadmap)                     | `poll`               |
| `notion`     | Notion database/pages (roadmap)                | `poll`               |
| `s3`         | objects under an S3 prefix (roadmap)           | `poll`, `event`      |

#### `connection` (object, required — schema depends on `type`)

**folder:**

```yaml
connection:
  path: /data/product-docs # required; absolute or ~-expanded
  include: ["**/*.md", "**/*.pdf"] # glob patterns; default ["**/*"]
  exclude: ["**/drafts/**"] # glob patterns; default []
```

**website:**

```yaml
connection:
  urls: # required; explicit page list
    - https://docs.example.com/setup
    - https://docs.example.com/calibration
```

**confluence (roadmap):**

```yaml
connection:
  base_url: https://co.atlassian.net/wiki
  space_key: TMC
  auth_env: CONFLUENCE_TOKEN # env var NAME holding the token (never the token)
```

#### `watch` (object, optional)

```yaml
watch:
  enabled: true # default false
  mode: filesystem # filesystem | poll | event; default poll
  interval_seconds: 300 # poll mode only; default 300
  recursive: true # folder/filesystem only; default true
```

- `filesystem` — real-time, OS-level file events (folder sources only). On
  create/modify/delete, only the affected file is re-indexed.
- `poll` — re-scan on an interval; fingerprint comparison ensures only changed
  documents are re-embedded. Works for any source type.
- `event` — webhook/callback-driven (roadmap; for sources that push change
  notifications, e.g. S3 event notifications, Confluence webhooks).

#### `chunking` (object, optional — inherits from `defaults`)

```yaml
chunking:
  strategy: markdown # recursive_character | markdown | token
  chunk_size: 800 # characters (or tokens for token strategy)
  chunk_overlap: 100
```

#### `embedding` (object, optional — inherits from `defaults`)

```yaml
embedding:
  provider: fastembed # fastembed | openai | voyage
  model: BAAI/bge-small-en-v1.5
  api_key_env: OPENAI_API_KEY # env var NAME; required for openai/voyage
```

- `fastembed` — fully local, ONNX, CPU-friendly, no API key. Default so the
  server runs out of the box.
- `openai` / `voyage` — hosted; read the key from the named env var at startup.

#### `vector_store` (object, optional except `collection`)

```yaml
vector_store:
  backend: chroma # chroma (default); qdrant/pgvector roadmap
  collection: docs_product # REQUIRED; unique per source
  persist_directory: ./vector_db
```

`collection` must be unique per source. The server validates at startup that no
two sources with different embedding configs share a collection.

#### `metadata` (object, optional)

Static key/value pairs stamped onto every chunk from this source. Useful for
filtering and citation (e.g. product line, doc version, access tier).

```yaml
metadata:
  product: example-product
  version: "3.x"
  audience: public
```

### 2.3 Complete example

```yaml
defaults:
  embedding:
    provider: fastembed
    model: BAAI/bge-small-en-v1.5
  vector_store:
    backend: chroma
    persist_directory: ./vector_db

sources:
  - name: product-docs
    type: folder
    connection:
      path: /data/product/docs
      include: ["**/*.md", "**/*.pdf"]
      exclude: ["**/internal/**"]
    watch:
      enabled: true
      mode: filesystem
      recursive: true
    chunking:
      strategy: markdown
      chunk_size: 1000
      chunk_overlap: 150
    vector_store:
      collection: product_docs
    metadata:
      product: example-product
      audience: public

  - name: troubleshooting-playbooks
    type: folder
    connection:
      path: /data/product/playbooks
      include: ["**/*.md"]
    watch:
      enabled: true
      mode: filesystem
    vector_store:
      collection: playbooks
    metadata:
      audience: support

  - name: release-notes
    type: website
    connection:
      urls:
        - https://docs.example.com/releases/latest
    watch:
      enabled: true
      mode: poll
      interval_seconds: 3600
    vector_store:
      collection: release_notes
```

---

## 3. MCP tool API (what the LLM can call)

Five tools, deliberately small. They never change as sources are added.

### 3.1 `search`

Primary tool. Semantic search across one or all configured sources.

```
search(
    query:  str,                # natural-language query (required)
    source: str | None = None,  # restrict to one source by name; None = all
    top_k:  int = 5,            # max results
    filter: dict | None = None, # optional metadata filter, e.g. {"version": "3.x"}
) -> list[SearchResult]
```

`SearchResult`:

```json
{
  "source": "product-docs",
  "doc_id": "setup/cameras.md",
  "content": "the matched chunk text ...",
  "metadata": {
    "rel_path": "setup/cameras.md",
    "chunk_index": 4,
    "product": "example-product",
    "audience": "public"
  },
  "score": 0.83 // normalized 0–1 similarity (1 = most similar)
}
```

Notes:

- When `source` is omitted, results from all sources are merged and sorted by
  score, then truncated to `top_k`.
- `filter` applies a metadata equality filter (e.g. restrict to a doc version
  or audience tier) — backed by the vector store's native `where` filtering.
- Returns `[]` (not an error) when nothing matches.

### 3.2 `list_sources`

Discovery tool. Lets the agent see what's available and whether it's healthy.

```
list_sources() -> list[SourceInfo]
```

`SourceInfo`:

```json
{
  "name": "product-docs",
  "type": "folder",
  "collection": "product_docs",
  "document_count": 142,
  "chunk_count": 1380,
  "last_indexed_at": 1718380800.0,
  "last_error": null,
  "watch_enabled": true,
  "watch_mode": "filesystem",
  "metadata": { "product": "example-product", "audience": "public" }
}
```

Useful both for ops visibility and for the agent to decide whether to scope a
search (e.g. route a "how do I calibrate" question to the playbooks source).

### 3.3 `get_document`

Fetch the full content of a document after `search` surfaces a relevant chunk —
when the agent needs more than the matched snippet.

```
get_document(
    source: str,    # source name (required)
    doc_id: str,    # document id from search result metadata (required)
) -> Document
```

`Document`:

```json
{
  "source": "product-docs",
  "doc_id": "setup/cameras.md",
  "content": "full document text ...",
  "metadata": { "rel_path": "setup/cameras.md", "product": "example-product" }
}
```

Returns `{"error": "..."}` if the source or document id is unknown.

### 3.4 `get_index_status`

Health/freshness check for one source or all of them. Lets the agent warn a
user when a source is mid-reindex or stale, and supports ops monitoring.

```
get_index_status(
    source_name: str | None = None,   # one source, or all if omitted
) -> list[IndexStatus]
```

`IndexStatus`:

```json
{
  "name": "product-docs",
  "indexing": false,
  "document_count": 142,
  "chunk_count": 1380,
  "last_indexed_at": 1718380800.0,
  "last_error": null
}
```

### 3.5 `reindex`

Force a full re-scan of a source (new/changed/deleted docs reconciled). Mostly
an admin/debugging affordance — "the docs just changed, refresh now" — but
exposed as a tool so an agent can act on an explicit user request.

```
reindex(
    source_name: str,   # source to re-index (required)
) -> IndexStatus        # post-reindex status
```

Returns `{"error": "..."}` for an unknown source.

### 3.6 Optional / roadmap tools

- `read_chunk_neighbors(source, doc_id, chunk_index, window=1)` — return the
  chunks immediately before/after a matched chunk for local context, cheaper
  than fetching the whole document. (Pattern borrowed from mcp-local-rag.)
- `ingest(source, content, doc_id, metadata)` — push a document into a source
  ad hoc, without it living in the configured origin. Off by default; only for
  sources explicitly marked `writable: true` in config.

---

## 4. Error & return conventions

- Tools return structured `{"error": "..."}` objects rather than raising, so the
  agent can recover conversationally (e.g. unknown source → it can call
  `list_sources` and retry).
- `search` returning `[]` is a valid "no matches" result, distinct from an error.
- Scores are normalized to 0–1 (1 = most similar) at the tool boundary, so the
  agent reasons about relevance consistently regardless of the underlying
  vector store's native distance metric.
- All write/maintenance tools (`reindex`, `ingest`) log the action with a
  timestamp for audit.

---

## 5. Startup & validation

On launch the server:

1. Loads and validates `config.yaml` (Pydantic). Fails fast on schema errors.
2. Validates cross-source invariants: unique source names; no two
   differently-embedded sources sharing a collection; required env vars present
   for hosted embedding providers.
3. Builds one `SourcePipeline` per source and runs an initial index.
4. Starts a watcher per source that has `watch.enabled: true`.
5. Begins serving MCP tools.

Initial indexing errors are recorded in each source's status (surfaced via
`get_index_status`) rather than crashing the server — a bad single source
shouldn't take down search for the others.

---

## 6. Why this shape

- **Stable tool surface, variable config** — the agent's contract never changes;
  onboarding a new client's knowledge base is "write a YAML file," which is the
  repeatability story for a productized offering.
- **Per-source isolation** — different embedding models / chunking / watch modes
  coexist safely, and one source failing doesn't degrade the others.
- **Loader/watcher registries** — adding a source type (Confluence, Notion, S3)
  is a new loader class + a registry entry; chunking, embedding, vector store,
  watching, and the entire MCP surface are reused unchanged.
- **Local-first defaults** — fastembed + Chroma means the server runs with zero
  external dependencies or API keys, then scales up to hosted embeddings /
  vector stores purely through config.
