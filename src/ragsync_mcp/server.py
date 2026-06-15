"""MCP tool layer and server startup.

Exposes the five stable tools (``search``, ``list_sources``, ``get_document``,
``get_index_status``, ``reindex``) over MCP. The tools are source-agnostic: they
read through ``SourcePipeline`` objects built from config and never touch loaders
or the vector store directly. This surface does not change as sources are added.

Tools return structured ``{"error": "..."}`` dicts rather than raising, so a
calling agent can recover conversationally (e.g. an unknown source → call
``list_sources`` and retry).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .config import AppConfig, SourceConfig
from .config_watch import ConfigFileWatcher
from .ingestion import SourcePipeline
from .watchers import BaseWatcher, build_watcher

logger = logging.getLogger("ragsync-mcp")

mcp = FastMCP("ragsync")

# Populated by initialize() at startup. Guarded by _RELOAD_LOCK against
# concurrent live reloads; tool reads snapshot the dicts so they don't need it.
PIPELINES: dict[str, SourcePipeline] = {}
_WATCHERS: dict[str, BaseWatcher] = {}
# Hash of each source's config, to detect which sources actually changed on
# reload (so unchanged sources aren't needlessly rebuilt and re-indexed).
_SOURCE_FINGERPRINTS: dict[str, str] = {}
_CONFIG_PATH: Optional[str] = None
_CONFIG_WATCHER: Optional[ConfigFileWatcher] = None
_RELOAD_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Startup / initialization
# ---------------------------------------------------------------------------


def initialize(config_path: str, watch_config: bool = True) -> None:
    """Load config, build a pipeline per source, index, and start watchers.

    A single source failing its initial index records the error in that source's
    status (surfaced via ``get_index_status``) rather than crashing the server —
    a bad source must not take down search for the others.

    When ``watch_config`` is true the config file itself is watched, so editing
    it adds/removes/rebuilds the affected sources live without a restart.
    """
    global _CONFIG_PATH, _CONFIG_WATCHER

    # Validate before tearing anything down, so a broken config can't wipe a
    # running server's state.
    config = AppConfig.from_yaml(config_path)
    _CONFIG_PATH = config_path

    with _RELOAD_LOCK:
        for name in list(PIPELINES):
            _remove_source(name)
        for source in config.sources:
            _add_source(source)

    logger.info("ready: %d source(s) configured", len(PIPELINES))

    if watch_config and _CONFIG_WATCHER is None:
        _CONFIG_WATCHER = ConfigFileWatcher(config_path, reload_config)
        _CONFIG_WATCHER.start()


def reload_config() -> None:
    """Re-read the config file and apply changes to the running server.

    Diffs sources by name and config hash:
      - removed sources have their watcher stopped and pipeline dropped;
      - new sources are built, indexed, and watched;
      - changed sources are rebuilt; unchanged sources are left untouched.

    A config that fails to parse/validate is logged and ignored — the server
    keeps running with its current sources.
    """
    if _CONFIG_PATH is None:
        return
    try:
        config = AppConfig.from_yaml(_CONFIG_PATH)
    except Exception as exc:
        logger.error("config reload skipped — invalid config: %s", exc)
        return

    with _RELOAD_LOCK:
        new_sources = {s.name: s for s in config.sources}

        for name in set(PIPELINES) - set(new_sources):
            logger.info("config reload: removing source '%s'", name)
            _remove_source(name)

        for name, source in new_sources.items():
            fingerprint = _source_fingerprint(source)
            if name not in PIPELINES:
                logger.info("config reload: adding source '%s'", name)
                _add_source(source, fingerprint)
            elif _SOURCE_FINGERPRINTS.get(name) != fingerprint:
                logger.info("config reload: rebuilding changed source '%s'", name)
                _remove_source(name)
                _add_source(source, fingerprint)
            # Unchanged sources are left running as-is.

    logger.info("config reload complete: %d source(s) configured", len(PIPELINES))


def shutdown() -> None:
    """Stop the config watcher and all source watchers (on exit / in tests)."""
    global _CONFIG_WATCHER
    if _CONFIG_WATCHER is not None:
        try:
            _CONFIG_WATCHER.stop()
        except Exception:
            logger.exception("error stopping config watcher")
        _CONFIG_WATCHER = None
    with _RELOAD_LOCK:
        for name in list(PIPELINES):
            _remove_source(name)


def _add_source(source: SourceConfig, fingerprint: Optional[str] = None) -> None:
    """Build, index, and start watching one source. Errors are non-fatal."""
    logger.info("initializing source '%s' (%s)", source.name, source.type)
    try:
        pipeline = SourcePipeline(source)
    except Exception:
        logger.exception("could not build source '%s'", source.name)
        return
    PIPELINES[source.name] = pipeline
    _SOURCE_FINGERPRINTS[source.name] = fingerprint or _source_fingerprint(source)
    pipeline.initial_index()
    try:
        watcher = build_watcher(pipeline)
    except Exception:
        logger.exception("could not start watcher for source '%s'", source.name)
        watcher = None
    if watcher is not None:
        watcher.start()
        _WATCHERS[source.name] = watcher


def _remove_source(name: str) -> None:
    """Stop a source's watcher and drop its pipeline (persisted data is kept)."""
    watcher = _WATCHERS.pop(name, None)
    if watcher is not None:
        try:
            watcher.stop()
        except Exception:
            logger.exception("error stopping watcher for source '%s'", name)
    PIPELINES.pop(name, None)
    _SOURCE_FINGERPRINTS.pop(name, None)


def _source_fingerprint(source: SourceConfig) -> str:
    """Stable hash of a source's resolved config, to detect real changes."""
    return json.dumps(source.model_dump(mode="json"), sort_keys=True)


def _get_pipeline(name: str) -> Optional[SourcePipeline]:
    return PIPELINES.get(name)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search(
    query: str,
    source: Optional[str] = None,
    top_k: int = 5,
    filter: Optional[dict] = None,
) -> list[dict]:
    """Semantic search across one or all configured knowledge sources.

    Args:
        query: Natural-language query.
        source: Restrict to one source by name. Omit to search all sources.
        top_k: Maximum number of results to return.
        filter: Optional metadata equality filter, e.g. {"version": "3.x"}.

    Returns a list of results sorted by descending score (1.0 = most similar),
    each with source, doc_id, content, metadata, and score. Returns [] when
    nothing matches. To search every source, leave `source` unset; to narrow to
    one, pass its name from list_sources.
    """
    if source is not None:
        pipeline = _get_pipeline(source)
        if pipeline is None:
            return [{"error": f"unknown source '{source}'. Call list_sources to see available sources."}]
        pipelines = [pipeline]
    else:
        pipelines = list(PIPELINES.values())

    results: list[dict] = []
    for pipeline in pipelines:
        try:
            results.extend(pipeline.search(query, top_k=top_k, filter=filter))
        except Exception as exc:
            logger.exception("search failed for source '%s'", pipeline.name)
            results.append({"error": f"search failed for source '{pipeline.name}': {exc}"})

    # Merge across sources and truncate to top_k by score.
    scored = [r for r in results if "score" in r]
    errors = [r for r in results if "error" in r]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return errors + scored[:top_k]


@mcp.tool()
def list_sources() -> list[dict]:
    """List configured sources with their health and metadata.

    Use this to discover what knowledge is available and decide whether to scope
    a search to a particular source. Each entry includes name, type, description,
    collection, document/chunk counts, last index time, watch settings, and any
    static metadata.
    """
    return [pipeline.source_info() for pipeline in PIPELINES.values()]


@mcp.tool()
def get_document(source: str, doc_id: str) -> dict:
    """Fetch the full content of a document surfaced by search.

    Use after `search` returns a relevant chunk and you need the whole document
    rather than the matched snippet.

    Args:
        source: Source name (from the search result's `source`).
        doc_id: Document id (from the search result's `doc_id`).

    Returns the document with full content and metadata, or {"error": "..."} if
    the source or document id is unknown.
    """
    pipeline = _get_pipeline(source)
    if pipeline is None:
        return {"error": f"unknown source '{source}'. Call list_sources to see available sources."}
    doc = pipeline.get_document(doc_id)
    if doc is None:
        return {"error": f"unknown document '{doc_id}' in source '{source}'."}
    return doc


@mcp.tool()
def get_index_status(source_name: Optional[str] = None) -> list[dict]:
    """Report indexing health/freshness for one source or all of them.

    Use to warn a user when a source is mid-reindex or stale. Each entry has
    name, indexing (bool), document_count, chunk_count, last_indexed_at, and
    last_error. Pass `source_name` for a single source, or omit for all.
    """
    if source_name is not None:
        pipeline = _get_pipeline(source_name)
        if pipeline is None:
            return [{"error": f"unknown source '{source_name}'."}]
        return [pipeline.status()]
    return [pipeline.status() for pipeline in PIPELINES.values()]


@mcp.tool()
def reindex(source_name: str) -> dict:
    """Force a full re-scan of a source (new/changed/deleted docs reconciled).

    An admin/refresh affordance — "the docs just changed, refresh now". Returns
    the post-reindex status, or {"error": "..."} for an unknown source.
    """
    pipeline = _get_pipeline(source_name)
    if pipeline is None:
        return {"error": f"unknown source '{source_name}'. Call list_sources to see available sources."}
    logger.info("manual reindex requested for source '%s'", source_name)
    try:
        return pipeline.full_reindex()
    except Exception as exc:
        return {"error": f"reindex failed for source '{source_name}': {exc}"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="ragsync RAG MCP server")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    args = parser.parse_args()

    try:
        initialize(args.config)
    except Exception:
        logger.exception("failed to initialize from config '%s'", args.config)
        sys.exit(1)

    try:
        mcp.run()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
