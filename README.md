# ragsync-mcp

A configuration-driven [Model Context Protocol](https://modelcontextprotocol.io)
server that ingests data from arbitrary sources, watches them for changes,
indexes them into vector stores, and exposes a small, stable set of tools an LLM
agent can call to search and retrieve that knowledge.

**Guiding principle:** everything that varies between deployments lives in a YAML
file; the tool surface the LLM sees never changes. Adding a source, swapping an
embedding model, or pointing at a different vector store is a config edit, not a
code change.

See [`DESIGN.md`](DESIGN.md) for the full specification and
[`AGENTS.md`](AGENTS.md) for contributor guidance.

## Install

```bash
uv sync                      # core dependencies
uv sync --extra openai       # optional hosted embedding provider
uv sync --extra dev          # test dependencies (pytest)
```

The default embedding provider, `fastembed`, runs locally (ONNX, CPU) and needs
no API key — the server works out of the box. Model weights download on first
run.

## Run

```bash
uv run ragsync-mcp --config examples/config.example.yaml
```

The server reads the config, builds one pipeline per source, runs an initial
index, starts a change watcher for each watched source, and begins serving MCP
tools over stdio.

### Live config reload

The config file itself is watched. Editing it applies changes without a restart:
new sources are built and indexed, removed sources are dropped, and changed
sources are rebuilt — unchanged sources keep running untouched. An edit that
fails validation is logged and ignored; the running server is never left in a
broken state.

## Use from an MCP client (Cursor, Claude, …)

The server speaks MCP over **stdio**, so any MCP-compatible client launches it as
a subprocess. Clients share the same `mcpServers` JSON shape; only the file
location differs:

| Client            | Config file                                                   |
| ----------------- | ------------------------------------------------------------- |
| Cursor            | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) |
| Claude Desktop    | `claude_desktop_config.json`                                  |
| Claude Code       | `.mcp.json` (or `claude mcp add`)                             |
| Windsurf / others | their `mcpServers` config                                     |

> **Paths inside the config are resolved against the config file's directory**
> (not the client's working directory), so a config can live in the repo and
> reference repo content with relative paths like `path: ./docs`. Give `--config`
> itself an **absolute** path, though — the client chooses where it launches the
> server from, so that's the one path it must be able to find unambiguously.

### Option A — run in place with uv (no install)

`uv run --directory` runs the server from the cloned repo without installing it:

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/abs/path/to/ragsync-mcp",
        "ragsync-mcp",
        "--config",
        "/abs/path/to/ragsync-mcp/examples/config.example.yaml"
      ]
    }
  }
}
```

### Option B — install the command, then reference it

```bash
uv tool install /abs/path/to/ragsync-mcp        # provides the `ragsync-mcp` command
```

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "ragsync-mcp",
      "args": ["--config", "/abs/path/to/config.yaml"]
    }
  }
}
```

(Equivalently, `"command": "python"`, `"args": ["-m", "ragsync_mcp", "--config", "…"]`
if the package is installed in the active environment.)

### Hosted embedding keys

For `openai`/`voyage` sources, the config names an env var (`api_key_env`) rather
than the key itself. Provide that variable to the subprocess via `env`:

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "ragsync-mcp",
      "args": ["--config", "/abs/path/to/config.yaml"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```

After saving, restart/reload the client. It will list the five tools (`search`,
`list_sources`, `get_document`, `get_index_status`, `reindex`); the agent calls
`search` to answer questions from your indexed sources. First launch downloads
the local embedding model, so initial startup can take a little longer.

## Configuration

A single YAML file defines global `defaults` and a list of `sources`. Each
source becomes one searchable collection with its own loader, chunking,
embedding model, vector-store collection, and watcher. Per-source isolation lets
different sources use different embedding models safely.

```yaml
defaults:
  chunking:
    { strategy: recursive_character, chunk_size: 800, chunk_overlap: 100 }
  embedding: { provider: fastembed, model: BAAI/bge-small-en-v1.5 }
  vector_store: { backend: chroma, persist_directory: ./vector_db }

sources:
  - name: product-docs
    type: folder
    description: Product documentation and how-to guides.
    connection:
      path: ./docs # relative to the config file's directory
      include: ["**/*.md"]
      exclude: ["**/internal/**"]
    watch: { enabled: true, mode: filesystem }
    chunking: { strategy: markdown, chunk_size: 1000, chunk_overlap: 150 }
    vector_store: { collection: product_docs }
    metadata: { product: example, audience: public }
```

The `examples/` directory has runnable configs:

- [`config.example.yaml`](examples/config.example.yaml) — a complete multi-source
  example (pointed at the sample content under `examples/docs` and
  `examples/playbooks`).
- [`folder.yaml`](examples/folder.yaml) — a single `folder` source.
- [`website.yaml`](examples/website.yaml) — a single `website` source.

### Source types

| type      | description                                    | watch modes          |
| --------- | ---------------------------------------------- | -------------------- |
| `folder`  | local/mounted directory of files (text, PDF)   | `filesystem`, `poll` |
| `website` | fixed list of web pages (fetched, not crawled) | `poll`               |

Include/exclude globs use gitignore-style matching (e.g. `**/internal/**`).

### Embedding providers

`fastembed` (local, default), `openai`, and `voyage` (hosted). Hosted providers
read their API key from the environment variable named by `api_key_env` — keys
are never written into config.

## MCP tools

Five tools, deliberately small and source-agnostic. They never change as sources
are added:

- **`search`** — semantic search across one or all sources, with optional
  metadata filtering. Returns results with normalized `[0, 1]` scores.
- **`list_sources`** — discover available sources and their health/metadata.
- **`get_document`** — fetch a full document after `search` surfaces a chunk.
- **`get_index_status`** — indexing freshness/health for one source or all.
- **`reindex`** — force a full re-scan of a source.

Tools return structured `{"error": "..."}` objects rather than raising, so the
calling agent can recover conversationally.

## Access scoping

Per-source isolation is a security boundary: scope access by running separate
server instances with separate configs. There is no cross-instance "search
everything" path.

## Development

```bash
uv run pytest
```

Tests run fully offline by injecting a deterministic embedder in place of
fastembed (see `tests/conftest.py`). The architecture and extension contract —
how to add a new source type — are documented in [`AGENTS.md`](AGENTS.md).
