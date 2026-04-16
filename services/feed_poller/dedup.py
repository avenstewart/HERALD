"""URL-level deduplication against Redis.

A SHA256 of the normalized URL is added to a Redis SET with a 30-day TTL on
the associated companion key, so URLs eventually re-qualify for re-ingestion
if a source republishes an updated article at the same URL.
"""

from __future__ import annotations

from collections.abc import Iterable

from redis.asyncio import Redis

from shared.settings import settings
from shared.utils import normalize_url, sha256_hex


async def filter_new_urls(redis: Redis, urls: Iterable[str]) -> list[tuple[str, str]]:
    """Return [(normalized_url, hash)] for URLs not already in the seen-set."""
    normalized = [(normalize_url(u), u) for u in urls if u]
    if not normalized:
        return []
    hashes = [sha256_hex(n) for n, _ in normalized]
    # SMISMEMBER is O(n) on Redis ≥ 6.2 — one round trip.
    membership = await redis.smismember(settings.seen_urls_set, hashes)
    fresh: list[tuple[str, str]] = []
    for (norm, _orig), h, seen in zip(normalized, hashes, membership, strict=True):
        if not seen:
            fresh.append((norm, h))
    return fresh


async def mark_seen(redis: Redis, pairs: list[tuple[str, str]]) -> None:
    """Mark (url, hash) pairs as seen + set a companion expire key for TTL sweep."""
    if not pairs:
        return
    ttl = settings.dedup_ttl_days * 24 * 3600
    async with redis.pipeline(transaction=False) as pipe:
        hashes = [h for _, h in pairs]
        pipe.sadd(settings.seen_urls_set, *hashes)
        for _, h in pairs:
            pipe.set(f"herald:url:{h}", "1", ex=ttl)
        await pipe.execute()
