"""asyncpg connection pool — shared by all MCP tool handlers."""

from __future__ import annotations

import asyncpg

from shared.settings import settings

_pool: asyncpg.Pool | None = None


def _asyncpg_url() -> str:
    """asyncpg uses the bare postgres scheme (no `+driver`)."""
    return settings.postgres_url.replace("postgresql+asyncpg", "postgresql", 1)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_asyncpg_url(), min_size=1, max_size=8)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
