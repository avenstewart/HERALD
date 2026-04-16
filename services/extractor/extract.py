"""Trafilatura-backed article extraction.

Extraction is CPU-bound and Trafilatura's API is synchronous, so extraction
runs in a thread pool executor from the async worker. Network fetches also
go through Trafilatura (which handles politeness headers + charset detection).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

import trafilatura

from shared.utils import content_hash, parse_date


@dataclass(slots=True)
class ExtractedArticle:
    url: str
    title: str | None
    content: str | None
    author: str | None
    published_at: datetime | None
    language: str
    word_count: int
    content_hash: str


def _extract_sync(url: str, timeout: int) -> ExtractedArticle | None:
    html = trafilatura.fetch_url(url)
    if not html:
        return None
    raw = trafilatura.extract(
        html,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not raw:
        return None
    data = json.loads(raw)
    text = data.get("text") or ""
    return ExtractedArticle(
        url=url,
        title=data.get("title"),
        content=text,
        author=data.get("author"),
        published_at=parse_date(data.get("date")),
        language=(data.get("language") or "en")[:8],
        word_count=len(text.split()),
        content_hash=content_hash(data.get("title"), text),
    )


async def extract(url: str, timeout: int = 15) -> ExtractedArticle | None:
    return await asyncio.to_thread(_extract_sync, url, timeout)
