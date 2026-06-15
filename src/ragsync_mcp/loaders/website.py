"""Website loader: a fixed list of web pages (fetched, not crawled).

``doc_id`` is the page URL — stable, unique, and reversible (``load_one`` re-fetches
that exact URL). HTML is parsed to readable text with BeautifulSoup; script and
style content is dropped. The fingerprint hashes the extracted text, so cosmetic
markup changes that don't alter visible text won't trigger a re-index.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

import httpx
from bs4 import BeautifulSoup

from ..config import WebsiteConnection
from .base import BaseLoader, RawDocument

logger = logging.getLogger("ragsync-mcp.loaders.website")

_TIMEOUT = httpx.Timeout(30.0)
_BLANK_LINES = re.compile(r"\n[ \t]*\n[ \t]*(\n[ \t]*)*")


class WebsiteLoader(BaseLoader):
    """Fetch and extract text from an explicit list of URLs."""

    connection: WebsiteConnection

    def load_all(self) -> Iterator[RawDocument]:
        for url in self.connection.urls:
            doc = self._fetch(url)
            if doc is not None:
                yield doc

    def load_one(self, doc_id: str) -> Optional[RawDocument]:
        if doc_id not in self.connection.urls:
            return None
        return self._fetch(doc_id)

    def _fetch(self, url: str) -> Optional[RawDocument]:
        try:
            response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("could not fetch %s: %s", url, exc)
            return None
        text = _html_to_text(response.text)
        title = _extract_title(response.text)
        return RawDocument(
            doc_id=url,
            content=text,
            metadata={"url": url, "title": title} if title else {"url": url},
        )


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML, dropping scripts/styles and blank runs."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines that tag stripping leaves behind.
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _extract_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None
