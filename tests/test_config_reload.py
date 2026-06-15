"""Live config-reload tests.

Covers the direct reload path (deterministic) and the file-watcher-triggered
path (timing-tolerant), plus the safety property that an invalid edit is
ignored and leaves the running server intact.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from ragsync_mcp import server


def _folder_source(name: str, collection: str, path: Path) -> dict:
    return {
        "name": name,
        "type": "folder",
        "connection": {"path": str(path), "include": ["**/*.md"]},
        "vector_store": {"collection": collection},
    }


def _write_config(path: Path, db: Path, sources: list[dict]) -> None:
    config = {
        "defaults": {"vector_store": {"persist_directory": str(db)}},
        "sources": sources,
    }
    path.write_text(yaml.safe_dump(config))


@pytest.fixture()
def workspace(tmp_path: Path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "doc.md").write_text("# A\n\nApple content about fruit.")
    b = tmp_path / "b"
    b.mkdir()
    (b / "doc.md").write_text("# B\n\nBanana content about fruit.")
    config = tmp_path / "config.yaml"
    db = tmp_path / "db"
    yield tmp_path, config, db, a, b
    server.shutdown()


def test_direct_reload_adds_and_removes_sources(workspace):
    tmp_path, config, db, a, b = workspace
    _write_config(config, db, [_folder_source("alpha", "alpha", a)])
    server.initialize(str(config), watch_config=False)
    assert set(server.PIPELINES) == {"alpha"}

    # Add a second source and reload.
    _write_config(
        config, db, [_folder_source("alpha", "alpha", a), _folder_source("beta", "beta", b)]
    )
    server.reload_config()
    assert set(server.PIPELINES) == {"alpha", "beta"}

    # Remove the first source and reload.
    _write_config(config, db, [_folder_source("beta", "beta", b)])
    server.reload_config()
    assert set(server.PIPELINES) == {"beta"}


def test_unchanged_source_pipeline_is_preserved(workspace):
    tmp_path, config, db, a, b = workspace
    _write_config(config, db, [_folder_source("alpha", "alpha", a)])
    server.initialize(str(config), watch_config=False)
    original = server.PIPELINES["alpha"]

    # Add beta; alpha is unchanged and must keep its exact pipeline object.
    _write_config(
        config, db, [_folder_source("alpha", "alpha", a), _folder_source("beta", "beta", b)]
    )
    server.reload_config()
    assert server.PIPELINES["alpha"] is original


def test_invalid_config_is_ignored(workspace):
    tmp_path, config, db, a, b = workspace
    _write_config(config, db, [_folder_source("alpha", "alpha", a)])
    server.initialize(str(config), watch_config=False)

    config.write_text("this: is: not: valid: yaml: [")
    server.reload_config()
    # Server keeps running with the previous source.
    assert set(server.PIPELINES) == {"alpha"}


def test_file_change_triggers_reload(workspace):
    tmp_path, config, db, a, b = workspace
    _write_config(config, db, [_folder_source("alpha", "alpha", a)])
    server.initialize(str(config), watch_config=True)
    assert set(server.PIPELINES) == {"alpha"}

    _write_config(
        config, db, [_folder_source("alpha", "alpha", a), _folder_source("beta", "beta", b)]
    )

    deadline = time.time() + 15
    while time.time() < deadline and set(server.PIPELINES) != {"alpha", "beta"}:
        time.sleep(0.2)
    assert set(server.PIPELINES) == {"alpha", "beta"}
