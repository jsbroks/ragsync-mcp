"""Config schema and cross-source validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ragsync_mcp.config import AppConfig, FolderConnection


def _base_source(**overrides) -> dict:
    src = {
        "name": "docs",
        "type": "folder",
        "connection": {"path": "/tmp/docs"},
        "vector_store": {"collection": "docs"},
    }
    src.update(overrides)
    return src


def test_defaults_are_inherited():
    cfg = AppConfig(
        defaults={"chunking": {"chunk_size": 1234}},
        sources=[_base_source()],
    )
    assert cfg.sources[0].chunking.chunk_size == 1234
    # Source without its own embedding inherits the default provider.
    assert cfg.sources[0].embedding.provider == "fastembed"


def test_source_overrides_default():
    cfg = AppConfig(
        defaults={"chunking": {"chunk_size": 1234}},
        sources=[_base_source(chunking={"chunk_size": 500, "chunk_overlap": 50})],
    )
    assert cfg.sources[0].chunking.chunk_size == 500


def test_collection_required():
    with pytest.raises(ValidationError):
        AppConfig(sources=[_base_source(vector_store={})])


def test_duplicate_source_names_rejected():
    with pytest.raises(ValidationError):
        AppConfig(sources=[_base_source(), _base_source()])


def test_shared_collection_with_different_embedding_rejected():
    with pytest.raises(ValidationError, match="corrupts search"):
        AppConfig(
            sources=[
                _base_source(name="a", embedding={"provider": "fastembed", "model": "m1"}),
                _base_source(name="b", embedding={"provider": "fastembed", "model": "m2"}),
            ]
        )


def test_shared_collection_with_same_embedding_allowed():
    cfg = AppConfig(
        sources=[
            _base_source(name="a"),
            _base_source(name="b"),
        ]
    )
    assert len(cfg.sources) == 2


def test_unknown_source_type_rejected():
    with pytest.raises(ValidationError):
        AppConfig(sources=[_base_source(type="ftp")])


def test_unknown_yaml_key_rejected():
    with pytest.raises(ValidationError):
        AppConfig(sources=[_base_source(typpo="oops")])


def test_hosted_provider_requires_key_env():
    with pytest.raises(ValidationError, match="api_key_env"):
        AppConfig(sources=[_base_source(embedding={"provider": "openai", "model": "x"})])


def test_relative_paths_resolved_against_config_dir(tmp_path: Path):
    cfg_dir = tmp_path / "conf"
    cfg_dir.mkdir()
    (cfg_dir / "docs").mkdir()
    config_path = cfg_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "defaults": {"vector_store": {"persist_directory": "./db"}},
                "sources": [
                    {
                        "name": "docs",
                        "type": "folder",
                        "connection": {"path": "./docs"},
                        "vector_store": {"collection": "docs"},
                    }
                ],
            }
        )
    )

    config = AppConfig.from_yaml(str(config_path))
    source = config.sources[0]
    assert isinstance(source.connection, FolderConnection)
    # Both paths are now absolute and anchored at the config file's directory.
    assert source.connection.path == str(cfg_dir / "docs")
    assert source.vector_store.persist_directory == str(cfg_dir / "db")


def test_absolute_paths_left_untouched(tmp_path: Path):
    abs_docs = tmp_path / "elsewhere" / "docs"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "docs",
                        "type": "folder",
                        "connection": {"path": str(abs_docs)},
                        "vector_store": {
                            "collection": "docs",
                            "persist_directory": str(tmp_path / "db"),
                        },
                    }
                ]
            }
        )
    )

    config = AppConfig.from_yaml(str(config_path))
    assert config.sources[0].connection.path == str(abs_docs)
