"""Feed poller.

Polls every configured source at its tier-defined interval, pushes unseen URLs
onto the Redis Stream consumed by the extractor workers. Each source polls
on its own asyncio task; a crash in one source does not affect the others.
"""

from __future__ import annotations

import asyncio
import signal

import feedparser
import httpx
from redis.asyncio import Redis

from services.feed_poller.config import Source, load_sources
from services.feed_poller.dedup import filter_new_urls, mark_seen
from shared.logging import configure_logging
from shared.settings import settings
from shared.utils import domain_of, utcnow

log = configure_logging("feed_poller")

USER_AGENT = "HERALD/0.1 (+https://github.com/) feed-poller"


async def fetch_feed(client: httpx.AsyncClient, url: str) -> feedparser.FeedParserDict:
    resp = await client.get(url, timeout=settings.extractor_fetch_timeout, follow_redirects=True)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


async def enqueue(redis: Redis, source: Source, normalized_url: str) -> None:
    await redis.xadd(
        settings.queue_stream_name,
        {
            "url": normalized_url,
            "source_name": source.name,
            "source_domain": domain_of(normalized_url),
            "category": source.category,
            "queued_at": utcnow().isoformat(),
        },
    )


async def poll_source_once(client: httpx.AsyncClient, redis: Redis, source: Source) -> int:
    try:
        url = source.resolve_url(settings.rsshub_url)
    except ValueError as e:
        log.error("source_config_invalid", source=source.name, error=str(e))
        return 0
    try:
        feed = await fetch_feed(client, url)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("feed_fetch_failed", source=source.name, url=url, error=str(e))
        return 0

    entry_urls = [entry.get("link") for entry in feed.entries if entry.get("link")]
    fresh = await filter_new_urls(redis, entry_urls)
    if not fresh:
        return 0
    for norm_url, _ in fresh:
        await enqueue(redis, source, norm_url)
    await mark_seen(redis, fresh)
    log.info("polled", source=source.name, new=len(fresh), total=len(entry_urls))
    return len(fresh)


async def run_source(client: httpx.AsyncClient, redis: Redis, source: Source) -> None:
    log.info("source_started", source=source.name, interval=source.interval_seconds, tier=source.tier)
    while True:
        try:
            await poll_source_once(client, redis, source)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — keep the loop alive on any failure
            log.exception("source_iteration_error", source=source.name, error=str(e))
        await asyncio.sleep(source.interval_seconds)


async def main() -> None:
    sources = load_sources(settings.sources_file)
    log.info("poller_starting", source_count=len(sources), redis=settings.redis_url)

    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    headers = {"User-Agent": USER_AGENT}

    stop = asyncio.Event()

    def _signal_handler(*_: object) -> None:
        log.info("shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [asyncio.create_task(run_source(client, redis, s), name=s.name) for s in sources]
        await stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    await redis.aclose()
    log.info("poller_stopped")


if __name__ == "__main__":
    asyncio.run(main())
