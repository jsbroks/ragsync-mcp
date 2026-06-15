"""Folder loader: a local/mounted directory of files.

``doc_id`` is the file path relative to the configured root (POSIX form), which
is stable, unique, reversible, and portable across machines (never an absolute
path — see AGENTS.md). Text files are read as UTF-8; ``.pdf`` files are
extracted with ``pypdf``. Files that can't be decoded are skipped with a warning
rather than crashing the index.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Optional

import pathspec

from ..config import FolderConnection
from .base import BaseLoader, RawDocument, content_hash

logger = logging.getLogger("ragsync-mcp.loaders.folder")


class FolderLoader(BaseLoader):
    """Read files under a directory, filtered by include/exclude globs."""

    connection: FolderConnection

    def __init__(self, connection: FolderConnection) -> None:
        super().__init__(connection)
        self.root = Path(connection.path).expanduser()
        # gitignore-style matching so patterns like "**/internal/**" behave as
        # users expect (pathlib's own glob does not match files under a
        # trailing "/**").
        self._include = pathspec.PathSpec.from_lines("gitignore", connection.include)
        self._exclude = pathspec.PathSpec.from_lines("gitignore", connection.exclude)

    # -- public API ---------------------------------------------------------

    def load_all(self) -> Iterator[RawDocument]:
        for path in self._scoped_files():
            doc = self._read(path)
            if doc is not None:
                yield doc

    def load_one(self, doc_id: str) -> Optional[RawDocument]:
        path = (self.root / doc_id).resolve()
        # Guard against traversal and out-of-scope ids.
        if not self._is_within_root(path) or path not in self._scoped_files():
            return None
        return self._read(path)

    def path_to_doc_id(self, path: str | Path) -> Optional[str]:
        """Map an absolute filesystem path to its doc_id, if in scope.

        Used by the filesystem watcher to translate OS events into doc ids.
        """
        resolved = Path(path).resolve()
        if not self._is_within_root(resolved):
            return None
        return resolved.relative_to(self.root.resolve()).as_posix()

    def is_in_scope(self, path: str | Path) -> bool:
        """Whether a path currently matches the include/exclude globs."""
        return Path(path).resolve() in self._scoped_files()

    # -- internals ----------------------------------------------------------

    def _scoped_files(self) -> set[Path]:
        """Walk the tree and keep files matching include but not exclude."""
        if not self.root.exists():
            logger.warning("folder source path does not exist: %s", self.root)
            return set()
        root = self.root.resolve()
        scoped: set[Path] = set()
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if self._include.match_file(rel) and not self._exclude.match_file(rel):
                scoped.add(path.resolve())
        return scoped

    def _is_within_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    def _read(self, path: Path) -> Optional[RawDocument]:
        rel = path.relative_to(self.root.resolve()).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            logger.warning("could not read %s: %s", rel, exc)
            return None

        if path.suffix.lower() == ".pdf":
            content = _extract_pdf(path, rel)
            if content is None:
                return None
        else:
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("skipping non-UTF-8 file: %s", rel)
                return None

        # Fingerprint the raw bytes so any byte-level change is detected even if
        # extraction is lossy.
        return RawDocument(
            doc_id=rel,
            content=content,
            metadata={"rel_path": rel},
            fingerprint=content_hash(raw.decode("utf-8", errors="replace")),
        )


def _extract_pdf(path: Path, rel: str) -> Optional[str]:
    """Extract text from a PDF, returning ``None`` on failure."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf raises a variety of errors on bad files
        logger.warning("could not extract PDF %s: %s", rel, exc)
        return None
