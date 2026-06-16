<div align="center">

<h1>ragsync</h1>

<p>Configuration-driven RAG MCP server — ingest, watch, and search arbitrary knowledge sources behind a stable tool surface.</p>

[![PyPI](https://img.shields.io/pypi/v/ragsync)](https://pypi.org/project/ragsync/)
[![CI](https://github.com/jsbroks/ragsync-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jsbroks/ragsync-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/ragsync)](https://pypi.org/project/ragsync/)

</div>

---

## Features

- **Broad source support** — index local folders (text, PDF, Markdown) and web pages; not limited to a single file type or format
- **Config-driven** — one YAML file defines sources, chunking strategy, embedding model, and vector store; no code required
- **Live reload** — filesystem watching and polling keep the index current as sources change; editing the config itself applies changes without a restart
- **Flexible embeddings** — local `fastembed` works out of the box with no API key; swap in OpenAI or Voyage per source
- **Stable MCP tool surface** — five source-agnostic tools (`search`, `list_sources`, `get_document`, `get_index_status`, `reindex`) that never change as sources are added

## Installation

The fastest way is with [`uvx`](https://docs.astral.sh/uv/) — no clone or install step:

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "uvx",
      "args": ["ragsync", "--config", "/abs/path/to/config.yaml"]
    }
  }
}
```

Add this to your MCP client config:

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

Pin a version with `"ragsync@0.2.0"` if you want reproducible launches. (If the
client can't find `uvx` on its `PATH`, use the absolute path to the `uvx` binary
— `which uvx`.)

<details>
<summary>Other install options</summary>

**Option B — run in place with uv (no install, from a clone)**

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
        "ragsync",
        "--config",
        "/abs/path/to/ragsync-mcp/examples/config.example.yaml"
      ]
    }
  }
}
```

**Option C — install the CLI globally**

```bash
uv tool install ragsync        # from PyPI; or a local path to a clone
```

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "ragsync",
      "args": ["--config", "/abs/path/to/config.yaml"]
    }
  }
}
```

(Equivalently, `"command": "python"`, `"args": ["-m", "ragsync_mcp", "--config", "…"]`
if the package is installed in the active environment.)

</details>

### Hosted embedding keys

For `openai`/`voyage` sources, the config names an env var (`api_key_env`) rather
than the key itself. Provide that variable to the subprocess via `env`:

```json
{
  "mcpServers": {
    "ragsync": {
      "command": "ragsync",
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
    strategy: recursive_character
    chunk_size: 800
    chunk_overlap: 100
  embedding:
    provider: fastembed
    model: BAAI/bge-small-en-v1.5 }
  vector_store:
    backend: chroma
    persist_directory: ./vector_db

sources:
  - name: product-docs
    type: folder
    description: Product documentation and how-to guides.
    connection:
      path: ./docs # relative to the config file's directory
      include: ["**/*.md"]
      exclude: ["**/internal/**"]
    watch:
      enabled: true
      mode: filesystem
    chunking:
      strategy: markdown
      chunk_size: 1000
      chunk_overlap: 150
    vector_store:
      collection: product_docs
    metadata:
      product: example
      audience: public
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
uv sync --extra dev          # install test dependencies
uv run pytest
```

Tests run fully offline by injecting a deterministic embedder in place of
fastembed (see `tests/conftest.py`). The architecture and extension contract —
how to add a new source type — are documented in [`AGENTS.md`](AGENTS.md).

## Releasing

Releases are automated from [Conventional Commits](https://www.conventionalcommits.org).
CI (`.github/workflows/ci.yml`) runs the test suite on every pull request. On
merge to `main`, the release workflow (`.github/workflows/release.yml`) runs the
tests again, then [python-semantic-release](https://python-semantic-release.readthedocs.io)
inspects the commits since the last tag and decides the next version:

| Commit type                                        | Example                       | Version bump            |
| -------------------------------------------------- | ----------------------------- | ----------------------- |
| `fix:`                                             | `fix: handle empty PDF pages` | patch — `0.1.0 → 0.1.1` |
| `feat:`                                            | `feat: add notion loader`     | minor — `0.1.0 → 0.2.0` |
| `feat!:` / `BREAKING CHANGE:`                      | `feat!: drop python 3.9`      | major — `0.1.0 → 1.0.0` |
| `docs:` / `chore:` / `test:` / `ci:` / `refactor:` | —                             | no release              |

When there is a releasable change it bumps `version` in `pyproject.toml`, updates
`CHANGELOG.md`, tags the commit, creates a GitHub release, and publishes the
package to PyPI. Once published, anyone can run it with
`uvx ragsync --config <path>` (or `pip install ragsync`).

**One-time setup** (repo maintainer):

1. On [PyPI](https://pypi.org/manage/account/publishing/), add a **Trusted
   Publisher** to the `ragsync` project: owner `jsbroks`, repository
   `ragsync-mcp`, workflow `release.yml`. This lets the workflow publish via OIDC
   with no stored token. (Alternatively, add a `PYPI_API_TOKEN` secret and set
   `password:` in the publish step.)
2. If `main` is a protected branch, allow the release workflow to push the
   version-bump commit (a repository ruleset bypass for `github-actions[bot]`, or
   a PAT with push access).
