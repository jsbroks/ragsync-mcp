---
name: readme-marketability
description: Design spec for README marketability improvements — centered header, badge row, Features section, and consolidated Installation section
metadata:
  type: project
---

# README Marketability Design

## Goal

Improve the cold-visitor experience on GitHub by adding a centered header with
badges, a Features section that communicates the differentiators clearly, and a
consolidated Installation section with the happy path front and center.

## Style

Clean and minimal. No decorative separators, no emoji bullets, no heavy
decoration. Centered header only; prose stays left-aligned.

## Section 1 — Header block

Replace the existing plain `# ragsync` heading with a centered `<div>` block:

```html
<div align="center">

<h1>ragsync</h1>

<p>Configuration-driven RAG MCP server — ingest, watch, and search arbitrary
knowledge sources behind a stable tool surface.</p>

[![PyPI](https://img.shields.io/pypi/v/ragsync)](https://pypi.org/project/ragsync/)
[![CI](https://github.com/jsbroks/ragsync-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jsbroks/ragsync-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/ragsync)](https://pypi.org/project/ragsync/)

</div>

---
```

Badges: PyPI version, CI status (links to the `ci.yml` workflow), Python
versions (derived from PyPI metadata).

## Section 2 — Features section

Insert a **Features** section immediately after the header block, before
**Configuration**:

```markdown
## Features

- **Broad source support** — index local folders (text, PDF, Markdown) and web
  pages; not limited to a single file type or format
- **Config-driven** — one YAML file defines sources, chunking strategy,
  embedding model, and vector store; no code required
- **Live reload** — filesystem watching and polling keep the index current as
  sources change; editing the config itself applies changes without a restart
- **Flexible embeddings** — local `fastembed` works out of the box with no API
  key; swap in OpenAI or Voyage per source
- **Stable MCP tool surface** — five source-agnostic tools (`search`,
  `list_sources`, `get_document`, `get_index_status`, `reindex`) that never
  change as sources are added
```

The lead bullet ("Broad source support") calls out the primary differentiator:
unlike markdown-only RAG servers, ragsync handles multiple source types and
keeps them live.

## Section 3 — Installation section

Consolidate the existing **Install** section and the **Use from an MCP client**
section into a single **Installation** section. Structure:

1. One-sentence intro + uvx MCP config snippet (the happy path)
2. Inline client config file table (Cursor, Claude Desktop, Claude Code,
   Windsurf)
3. Path resolution note (absolute path for `--config`)
4. `<details>` block collapsing Option B (run in place) and Option C (install
   CLI globally) for less common cases
5. Hosted embedding keys sub-section (unchanged content, stays outside the
   `<details>`)

The `uv sync` developer install (currently under **Install**) moves to the
**Development** section where it belongs.

## What does NOT change

- **Configuration** section — content and YAML examples unchanged
- **MCP tools** section — unchanged
- **Access scoping** section — unchanged
- **Development** section — gains the `uv sync` install line, otherwise unchanged
- **Releasing** section — unchanged
