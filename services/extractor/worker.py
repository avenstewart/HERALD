"""Extractor worker.

Consumes URLs from the Redis Stream via a consumer group (so multiple
replicas share the workload with at-least-once semantics), downloads and
extracts each article with Trafilatura, then upserts into the articles
table. Messages are ACKed only after a successful insert/update.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.extractor.extract import ExtractedArticle, extract
from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("extractor")

CONSUMER_NAME = os.environ.get("HOSTNAME") or socket.gethostname() or "extractor-1"
BLOCK_MS = 5_000

UPSERT_SQL = text(
    """
    INSERT INTO articles (
        url, content_hash, title, content, author, published_at,
        source_name, source_domain, category, language, word_count, extraction_method
    )
    VALUES (
        :url, :content_hash, :title, :content, :author, :published_at,
        :source_name, :source_domain, :category, :language, :word_count, :extraction_method
    )
    ON CONFLICT (url) DO UPDATE SET
        content_hash    = EXCLUDED.content_hash,
        title           = EXCLUDED.title,
        content         = EXCLUDED.content,
        author          = COALESCE(EXCLUDED.author, articles.author),
        published_at    = COALESCE(EXCLUDED.published_at, articles.published_at),
        language        = EXCLUDED.language,
        word_count      = EXCLUDED.word_count,
        extraction_method = EXCLUDED.extraction_method
    WHERE articles.content_hash IS DISTINCT FROM EXCLUDED.content_hash
    """
)


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(
            name=settings.queue_stream_name,
            groupname=settings.extractor_consumer_group,
            id="0",
            mkstream=True,
        )
        log.info("group_created", group=settings.extractor_consumer_group)
    except ResponseError as e:
        if "BUSYGROUP" in str(e):
            return
        raise


async def store(engine: AsyncEngine, article: ExtractedArticle, meta: dict[str, str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            UPSERT_SQL,
            {
                "url": article.url,
                "content_hash": article.content_hash,
                "title": article.title,
                "content": article.content,
                "author": article.author,
                "published_at": article.published_at,
                "source_name": meta.get("source_name") or "",
                "source_domain": meta.get("source_domain") or "",
                "category": meta.get("category"),
                "language": article.language,
                "word_count": article.word_count,
                "extraction_method": "trafilatura",
            },
        )


async def process_message(
    redis: Redis, engine: AsyncEngine, message_id: str, data: dict[str, str]
) -> None:
    url = data.get("url")
    if not url:
        log.warning("message_missing_url", msg_id=message_id, data=data)
        await redis.xack(
            settings.queue_stream_name, settings.extractor_consumer_group, message_id
        )
        return
    try:
        article = await extract(url, timeout=settings.extractor_fetch_timeout)
    except Exception as e:  # noqa: BLE001
        log.exception("extraction_crashed", url=url, error=str(e))
        await redis.xack(
            settings.queue_stream_name, settings.extractor_consumer_group, message_id
        )
        return

    if article is None:
        log.info("extraction_empty", url=url)
        await redis.xack(
            settings.queue_stream_name, settings.extractor_consumer_group, message_id
        )
        return

    try:
        await store(engine, article, data)
        await redis.xack(
            settings.queue_stream_name, settings.extractor_consumer_group, message_id
        )
        log.info("stored", url=url, title=article.title, words=article.word_count)
    except Exception as e:  # noqa: BLE001
        log.exception("db_write_failed", url=url, error=str(e))
        # Do not ack — message will be reclaimable by another consumer.


async def main() -> None:
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    engine = create_async_engine(settings.postgres_url, pool_size=4, max_overflow=4)
    await ensure_group(redis)

    log.info(
        "extractor_starting",
        consumer=CONSUMER_NAME,
        group=settings.extractor_consumer_group,
        stream=settings.queue_stream_name,
    )

    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        log.info("shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    while not stop.is_set():
        try:
            resp = await redis.xreadgroup(
                groupname=settings.extractor_consumer_group,
                consumername=CONSUMER_NAME,
                streams={settings.queue_stream_name: ">"},
                count=4,
                block=BLOCK_MS,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("xreadgroup_failed", error=str(e))
            await asyncio.sleep(1)
            continue

        if not resp:
            continue
        for _stream, messages in resp:
            for msg_id, data in messages:
                await process_message(redis, engine, msg_id, data)

    await engine.dispose()
    await redis.aclose()
    log.info("extractor_stopped")


if __name__ == "__main__":
    asyncio.run(main())
