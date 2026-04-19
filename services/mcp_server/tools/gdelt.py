"""MCP tools backed by GDELT v2 hypertables and the GDELT DOC 2.0 API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from services.mcp_server.db import get_timescale_pool
from services.mcp_server.gdelt_client import DOC_API_URL, get_doc_client
from shared.utils import parse_date


# ── result schemas ───────────────────────────────────────────────────────────


class GdeltEventResult(BaseModel):
    event_id: str
    event_date: datetime
    actor1_name: str | None
    actor1_country: str | None
    actor2_name: str | None
    actor2_country: str | None
    cameo_code: str
    cameo_root_code: str
    cameo_label: str | None
    goldstein_scale: float | None
    num_mentions: int | None
    num_sources: int | None
    num_articles: int | None
    avg_tone: float | None
    geo_fullname: str | None
    geo_country: str | None
    geo_lat: float | None
    geo_lon: float | None
    source_url: str | None


class GdeltGKGResult(BaseModel):
    record_id: str
    record_date: datetime
    source_url: str
    source_name: str | None
    themes: list[str]
    persons: list[str]
    organizations: list[str]
    locations: list[str]
    tone: float | None
    word_count: int | None


class ToneDataPoint(BaseModel):
    bucket: datetime
    article_count: int
    avg_tone: float | None


class EntityFrequency(BaseModel):
    name: str
    mentions: int


# ── helpers ──────────────────────────────────────────────────────────────────


def _bucket_width(resolution: str) -> str:
    return {
        "minute": "1 minute",
        "hour": "1 hour",
        "day": "1 day",
    }.get(resolution, "1 hour")


def _require_date(value: str, label: str) -> datetime:
    dt = parse_date(value)
    if dt is None:
        raise ValueError(f"Could not parse {label}={value!r} as a date")
    return dt


# ── tool registration ───────────────────────────────────────────────────────


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_gdelt_events(
        start_date: Annotated[str, Field(description="ISO-8601 start of window")],
        end_date: Annotated[str, Field(description="ISO-8601 end of window")],
        actor1_country: Annotated[str | None, Field(description="FIPS 2-char code")] = None,
        actor2_country: str | None = None,
        cameo_root_code: Annotated[str | None, Field(description="CAMEO root code e.g. '14' (PROTEST)")] = None,
        min_goldstein: float | None = None,
        max_goldstein: float | None = None,
        min_mentions: int | None = None,
        limit: int = 50,
    ) -> list[GdeltEventResult]:
        """Query structured CAMEO-coded events in a time window."""
        sd = _require_date(start_date, "start_date")
        ed = _require_date(end_date, "end_date")
        clauses = ["event_date >= $1", "event_date <= $2"]
        params: list[Any] = [sd, ed]
        if actor1_country:
            params.append(actor1_country.upper())
            clauses.append(f"actor1_country = ${len(params)}")
        if actor2_country:
            params.append(actor2_country.upper())
            clauses.append(f"actor2_country = ${len(params)}")
        if cameo_root_code:
            params.append(cameo_root_code)
            clauses.append(f"cameo_root_code = ${len(params)}")
        if min_goldstein is not None:
            params.append(min_goldstein)
            clauses.append(f"goldstein_scale >= ${len(params)}")
        if max_goldstein is not None:
            params.append(max_goldstein)
            clauses.append(f"goldstein_scale <= ${len(params)}")
        if min_mentions is not None:
            params.append(min_mentions)
            clauses.append(f"num_mentions >= ${len(params)}")
        params.append(limit)
        sql = f"""
            SELECT event_id, event_date, actor1_name, actor1_country,
                   actor2_name, actor2_country,
                   cameo_code, cameo_root_code, cameo_label,
                   goldstein_scale, num_mentions, num_sources, num_articles,
                   avg_tone, geo_fullname, geo_country, geo_lat, geo_lon,
                   source_url
            FROM gdelt_events
            WHERE {' AND '.join(clauses)}
            ORDER BY num_mentions DESC NULLS LAST, event_date DESC
            LIMIT ${len(params)}
        """
        pool = await get_timescale_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [GdeltEventResult(**dict(r)) for r in rows]

    @mcp.tool()
    async def get_gdelt_themes(
        themes: Annotated[list[str], Field(description="GKG theme codes, e.g. ['ECON_INFLATION']")],
        start_date: str,
        end_date: str,
        limit: int = 50,
    ) -> list[GdeltGKGResult]:
        """Find GKG records whose `themes` array overlaps any of the given codes."""
        sd = _require_date(start_date, "start_date")
        ed = _require_date(end_date, "end_date")
        pool = await get_timescale_pool()
        sql = """
            SELECT record_id, record_date, source_url, source_name,
                   themes, persons, organizations, locations,
                   tone, word_count
            FROM gdelt_gkg
            WHERE record_date >= $1 AND record_date <= $2
              AND themes && $3::text[]
            ORDER BY record_date DESC
            LIMIT $4
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, sd, ed, themes, limit)
        return [GdeltGKGResult(**dict(r)) for r in rows]

    @mcp.tool()
    async def get_gdelt_tone_timeline(
        start_date: str,
        end_date: str,
        themes: list[str] | None = None,
        resolution: Annotated[str, Field(description="'minute' | 'hour' | 'day'")] = "hour",
    ) -> list[ToneDataPoint]:
        """Average media tone and article volume bucketed over time."""
        sd = _require_date(start_date, "start_date")
        ed = _require_date(end_date, "end_date")
        width = _bucket_width(resolution)
        params: list[Any] = [sd, ed]
        where_theme = ""
        if themes:
            params.append(themes)
            where_theme = f" AND themes && ${len(params)}::text[]"
        sql = f"""
            SELECT time_bucket(INTERVAL '{width}', record_date) AS bucket,
                   COUNT(*)::int AS article_count,
                   AVG(tone) AS avg_tone
            FROM gdelt_gkg
            WHERE record_date >= $1 AND record_date <= $2{where_theme}
            GROUP BY bucket
            ORDER BY bucket
        """
        pool = await get_timescale_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [ToneDataPoint(**dict(r)) for r in rows]

    @mcp.tool()
    async def get_gdelt_entities(
        entity_type: Annotated[
            str, Field(description="'person' | 'organization' | 'location'")
        ],
        start_date: str,
        end_date: str,
        min_mentions: int = 5,
        limit: int = 50,
    ) -> list[EntityFrequency]:
        """Top-N most-mentioned persons / orgs / locations in a window."""
        col = {
            "person": "persons",
            "organization": "organizations",
            "location": "locations",
        }.get(entity_type)
        if col is None:
            raise ValueError(f"entity_type must be person/organization/location, got {entity_type!r}")
        sd = _require_date(start_date, "start_date")
        ed = _require_date(end_date, "end_date")
        pool = await get_timescale_pool()
        sql = f"""
            SELECT name, COUNT(*)::int AS mentions
            FROM gdelt_gkg, unnest({col}) AS name
            WHERE record_date >= $1 AND record_date <= $2
            GROUP BY name
            HAVING COUNT(*) >= $3
            ORDER BY mentions DESC
            LIMIT $4
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, sd, ed, min_mentions, limit)
        return [EntityFrequency(**dict(r)) for r in rows]

    @mcp.tool()
    async def gdelt_doc_search(
        query: Annotated[str, Field(description="GDELT DOC 2.0 query (theme:X, tone<-5, sourcecountry:US, \"phrase\")")],
        timespan: Annotated[str, Field(description="e.g. '2h', '7d', '3m' (max 3 months)")] = "24h",
        mode: Annotated[str, Field(description="artlist | timelinevol | timelinetone")] = "artlist",
        max_records: int = 75,
    ) -> dict:
        """Proxy to GDELT's DOC 2.0 API — searches GDELT's 3-month rolling full-text index."""
        params = {
            "query": query,
            "mode": mode,
            "timespan": timespan,
            "maxrecords": str(max_records),
            "format": "json",
        }
        client = get_doc_client()
        resp = await client.get(DOC_API_URL, params=params)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            # DOC API sometimes returns non-JSON on empty timespans.
            return {"raw": resp.text[:2000]}
