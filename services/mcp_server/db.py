"""asyncpg connection pools — one per database target.

The MCP server queries both the articles Postgres (for article tools) and the
GDELT TimescaleDB (for event/GKG tools).
"""

from __future__ import annotations

import asyncpg

from shared.settings import settings

_pg_pool: asyncpg.Pool | None = None
_ts_pool: asyncpg.Pool | None = None


def _asyncpg_url(sa_url: str) -> str:
    """Strip SQLAlchemy's `+asyncpg` driver suffix — asyncpg wants a bare URL."""
    return sa_url.replace("postgresql+asyncpg", "postgresql", 1)


async def get_pool() -> asyncpg.Pool:
    """Articles Postgres pool."""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(
            _asyncpg_url(settings.postgres_url), min_size=1, max_size=8
        )
    return _pg_pool


async def get_timescale_pool() -> asyncpg.Pool:
    """GDELT TimescaleDB pool."""
    global _ts_pool
    if _ts_pool is None:
        _ts_pool = await asyncpg.create_pool(
            _asyncpg_url(settings.timescale_url), min_size=1, max_size=8
        )
    return _ts_pool


async def close_pool() -> None:
    global _pg_pool, _ts_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
    if _ts_pool is not None:
        await _ts_pool.close()
        _ts_pool = None
