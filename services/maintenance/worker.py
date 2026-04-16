"""Daily retention enforcement for the articles table.

Two policies, configurable via env:
  * RETENTION_CONTENT_DAYS  — NULL out `content` after this many days
                              (defaults to 180, i.e. 6 months).
  * RETENTION_METADATA_DAYS — DELETE entire rows after this many days
                              (defaults to 730, i.e. 2 years).

GDELT hypertables use TimescaleDB's built-in `add_retention_policy()` —
plain-Postgres `articles` has no such machinery, hence this service.
"""

from __future__ import annotations

import asyncio
import os
import signal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("maintenance")

RUN_INTERVAL_SECONDS = int(os.environ.get("MAINTENANCE_INTERVAL_SECONDS", str(24 * 3600)))
RETENTION_CONTENT_DAYS = int(os.environ.get("RETENTION_CONTENT_DAYS", "180"))
RETENTION_METADATA_DAYS = int(os.environ.get("RETENTION_METADATA_DAYS", "730"))

TRUNCATE_SQL = text(
    """
    UPDATE articles
    SET content = NULL
    WHERE content IS NOT NULL
      AND ingested_at < NOW() - make_interval(days => :days)
    """
)

DELETE_SQL = text(
    """
    DELETE FROM articles
    WHERE ingested_at < NOW() - make_interval(days => :days)
    """
)


async def run_once(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        r1 = await conn.execute(TRUNCATE_SQL, {"days": RETENTION_CONTENT_DAYS})
        r2 = await conn.execute(DELETE_SQL, {"days": RETENTION_METADATA_DAYS})
    log.info(
        "retention_applied",
        content_truncated=r1.rowcount,
        rows_deleted=r2.rowcount,
        content_days=RETENTION_CONTENT_DAYS,
        metadata_days=RETENTION_METADATA_DAYS,
    )


async def main() -> None:
    log.info(
        "maintenance_starting",
        interval=RUN_INTERVAL_SECONDS,
        content_days=RETENTION_CONTENT_DAYS,
        metadata_days=RETENTION_METADATA_DAYS,
    )
    engine = create_async_engine(settings.postgres_url, pool_size=1, max_overflow=1)

    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        log.info("shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    while not stop.is_set():
        try:
            await run_once(engine)
        except Exception as e:  # noqa: BLE001
            log.exception("retention_failed", error=str(e))
        try:
            await asyncio.wait_for(stop.wait(), timeout=RUN_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue

    await engine.dispose()
    log.info("maintenance_stopped")


if __name__ == "__main__":
    asyncio.run(main())
