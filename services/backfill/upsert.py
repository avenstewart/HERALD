"""DB writer for backfilled articles — same shape as the live extractor upsert,
minus the Redis ack flow. Uses sync psycopg3 since Fundus is a sync iterator.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql

from shared.settings import settings
from shared.utils import content_hash, domain_of, normalize_url

UPSERT_SQL = sql.SQL(
    """
    INSERT INTO articles (
        url, content_hash, title, content, author, published_at,
        source_name, source_domain, category, language, word_count, extraction_method
    )
    VALUES (
        %(url)s, %(content_hash)s, %(title)s, %(content)s, %(author)s, %(published_at)s,
        %(source_name)s, %(source_domain)s, %(category)s, %(language)s, %(word_count)s,
        %(extraction_method)s
    )
    ON CONFLICT (url) DO UPDATE SET
        content_hash    = EXCLUDED.content_hash,
        title           = EXCLUDED.title,
        content         = EXCLUDED.content,
        author          = COALESCE(EXCLUDED.author, articles.author),
        published_at    = COALESCE(EXCLUDED.published_at, articles.published_at),
        word_count      = EXCLUDED.word_count,
        extraction_method = EXCLUDED.extraction_method
    WHERE articles.content_hash IS DISTINCT FROM EXCLUDED.content_hash
    """
)


def _sync_pg_url() -> str:
    return settings.postgres_url_sync.replace("postgresql+psycopg", "postgresql", 1)


def connect() -> psycopg.Connection:
    return psycopg.connect(_sync_pg_url(), autocommit=False)


def _normalize_date(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def upsert_article(
    conn: psycopg.Connection,
    *,
    url: str,
    title: str | None,
    content: str | None,
    author: str | None,
    published_at: datetime | None,
    source_name: str,
    language: str = "en",
    extraction_method: str,
    category: str | None = None,
) -> bool:
    """Upsert a single article. Returns True if a write occurred, False if skipped."""
    if not url or not content:
        return False
    norm = normalize_url(url)
    params = {
        "url": norm,
        "content_hash": content_hash(title, content),
        "title": title,
        "content": content,
        "author": author,
        "published_at": _normalize_date(published_at),
        "source_name": source_name,
        "source_domain": domain_of(norm),
        "category": category,
        "language": (language or "en")[:8],
        "word_count": len(content.split()),
        "extraction_method": extraction_method,
    }
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, params)
        affected = cur.rowcount
    conn.commit()
    return affected > 0
