"""MCP tools backed by the `articles` Postgres table.

GDELT-backed tools land in a future release; their schemas are sketched in
`HERALD_design.md` so consuming agents can plan around them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from services.mcp_server.db import get_pool
from shared.settings import settings
from shared.utils import parse_date


class ArticleResult(BaseModel):
    id: str
    url: str
    title: str | None
    snippet: str | None
    author: str | None
    published_at: datetime | None
    ingested_at: datetime
    source_name: str
    source_domain: str
    category: str | None
    language: str
    word_count: int | None


class SourceInfo(BaseModel):
    name: str
    tier: str
    category: str
    url: str | None
    rsshub_route: str | None
    last_ingested_at: datetime | None
    article_count_24h: int


class VolumeDataPoint(BaseModel):
    bucket: datetime
    article_count: int


class IngestionStatus(BaseModel):
    queue_depth: int
    pending_messages: int
    article_count_total: int
    article_count_24h: int
    last_article_ingested_at: datetime | None
    sources_configured: int


def _row_to_result(row: dict[str, Any]) -> ArticleResult:
    content = row.get("content") or ""
    return ArticleResult(
        id=str(row["id"]),
        url=row["url"],
        title=row.get("title"),
        snippet=(content[:400] + "…") if len(content) > 400 else (content or None),
        author=row.get("author"),
        published_at=row.get("published_at"),
        ingested_at=row["ingested_at"],
        source_name=row["source_name"],
        source_domain=row["source_domain"],
        category=row.get("category"),
        language=row["language"],
        word_count=row.get("word_count"),
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_articles(
        query: Annotated[str, Field(description="Full-text query — Postgres tsquery syntax accepted")],
        start_date: Annotated[str | None, Field(description="ISO-8601 lower bound on ingested_at")] = None,
        end_date: Annotated[str | None, Field(description="ISO-8601 upper bound on ingested_at")] = None,
        sources: Annotated[list[str] | None, Field(description="Filter by source_domain")] = None,
        categories: Annotated[list[str] | None, Field(description="Filter by category")] = None,
        language: str = "en",
        limit: int = 20,
    ) -> list[ArticleResult]:
        """Full-text search over ingested articles using Postgres tsvector."""
        pool = await get_pool()
        clauses = ["search_vector @@ websearch_to_tsquery('english', $1)", "language = $2"]
        params: list[Any] = [query, language]
        if start_date and (sd := parse_date(start_date)):
            params.append(sd)
            clauses.append(f"ingested_at >= ${len(params)}")
        if end_date and (ed := parse_date(end_date)):
            params.append(ed)
            clauses.append(f"ingested_at <= ${len(params)}")
        if sources:
            params.append(sources)
            clauses.append(f"source_domain = ANY(${len(params)})")
        if categories:
            params.append(categories)
            clauses.append(f"category = ANY(${len(params)})")
        params.append(limit)
        sql = f"""
            SELECT id, url, title, content, author, published_at, ingested_at,
                   source_name, source_domain, category, language, word_count,
                   ts_rank(search_vector, websearch_to_tsquery('english', $1)) AS rank
            FROM articles
            WHERE {' AND '.join(clauses)}
            ORDER BY rank DESC, ingested_at DESC
            LIMIT ${len(params)}
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_result(dict(r)) for r in rows]

    @mcp.tool()
    async def get_recent_articles(
        lookback_minutes: int = 60,
        sources: list[str] | None = None,
        categories: list[str] | None = None,
        limit: int = 50,
    ) -> list[ArticleResult]:
        """Return articles ingested in the last N minutes, newest first."""
        pool = await get_pool()
        clauses = [f"ingested_at >= NOW() - INTERVAL '{int(lookback_minutes)} minutes'"]
        params: list[Any] = []
        if sources:
            params.append(sources)
            clauses.append(f"source_domain = ANY(${len(params)})")
        if categories:
            params.append(categories)
            clauses.append(f"category = ANY(${len(params)})")
        params.append(limit)
        sql = f"""
            SELECT id, url, title, content, author, published_at, ingested_at,
                   source_name, source_domain, category, language, word_count
            FROM articles
            WHERE {' AND '.join(clauses)}
            ORDER BY ingested_at DESC
            LIMIT ${len(params)}
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_result(dict(r)) for r in rows]

    @mcp.tool()
    async def get_article_volume(
        start_date: Annotated[str, Field(description="ISO-8601 lower bound on ingested_at")],
        end_date: Annotated[str, Field(description="ISO-8601 upper bound on ingested_at")],
        query: Annotated[
            str | None,
            Field(description="Optional full-text filter (websearch_to_tsquery syntax)"),
        ] = None,
        resolution: Annotated[
            str, Field(description="'minute' | 'hour' | 'day'")
        ] = "hour",
    ) -> list[VolumeDataPoint]:
        """Time-series of article counts — useful for detecting news volume spikes."""
        sd = parse_date(start_date)
        ed = parse_date(end_date)
        if sd is None or ed is None:
            raise ValueError("start_date and end_date must be ISO-8601 parseable")
        trunc = {"minute": "minute", "hour": "hour", "day": "day"}.get(resolution, "hour")
        params: list[Any] = [sd, ed]
        query_clause = ""
        if query:
            params.append(query)
            query_clause = f" AND search_vector @@ websearch_to_tsquery('english', ${len(params)})"
        sql = f"""
            SELECT date_trunc('{trunc}', ingested_at) AS bucket,
                   COUNT(*)::int AS article_count
            FROM articles
            WHERE ingested_at >= $1 AND ingested_at <= $2{query_clause}
            GROUP BY bucket
            ORDER BY bucket
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [VolumeDataPoint(**dict(r)) for r in rows]

    @mcp.tool()
    async def get_article_by_url(url: str) -> ArticleResult | None:
        """Fetch a specific article by its (normalized) URL."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, url, title, content, author, published_at, ingested_at,
                       source_name, source_domain, category, language, word_count
                FROM articles
                WHERE url = $1
                """,
                url,
            )
        return _row_to_result(dict(row)) if row else None

    @mcp.tool()
    async def list_sources() -> list[SourceInfo]:
        """Return every configured source with its tier, category, and recent activity."""
        path: Path = settings.sources_file
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        pool = await get_pool()
        async with pool.acquire() as conn:
            stats = await conn.fetch(
                """
                SELECT source_name,
                       MAX(ingested_at) AS last_ingested_at,
                       COUNT(*) FILTER (WHERE ingested_at >= NOW() - INTERVAL '24 hours') AS c24
                FROM articles
                GROUP BY source_name
                """
            )
        by_name = {r["source_name"]: r for r in stats}

        out: list[SourceInfo] = []
        for s in raw.get("sources", []):
            r = by_name.get(s["name"])
            out.append(
                SourceInfo(
                    name=s["name"],
                    tier=s["tier"],
                    category=s.get("category", "general"),
                    url=s.get("url"),
                    rsshub_route=s.get("rsshub_route"),
                    last_ingested_at=r["last_ingested_at"] if r else None,
                    article_count_24h=int(r["c24"]) if r else 0,
                )
            )
        return out

    @mcp.tool()
    async def get_ingestion_status() -> IngestionStatus:
        """Queue depth, pending messages, and article counts."""
        from redis.asyncio import Redis

        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            try:
                stream_len = int(await redis.xlen(settings.queue_stream_name))
            except Exception:  # noqa: BLE001 — stream may not exist yet
                stream_len = 0
            try:
                pending_info = await redis.xpending(
                    settings.queue_stream_name, settings.extractor_consumer_group
                )
                pending = int(pending_info.get("pending", 0)) if isinstance(pending_info, dict) else 0
            except Exception:  # noqa: BLE001
                pending = 0
        finally:
            await redis.aclose()

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE ingested_at >= NOW() - INTERVAL '24 hours') AS c24,
                       MAX(ingested_at) AS latest
                FROM articles
                """
            )

        sources_configured = 0
        if settings.sources_file.exists():
            with settings.sources_file.open("r", encoding="utf-8") as f:
                sources_configured = len((yaml.safe_load(f) or {}).get("sources", []))

        return IngestionStatus(
            queue_depth=stream_len,
            pending_messages=pending,
            article_count_total=int(row["total"]),
            article_count_24h=int(row["c24"]),
            last_article_ingested_at=row["latest"],
            sources_configured=sources_configured,
        )
