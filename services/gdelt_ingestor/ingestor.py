"""GDELT v2 ingestor.

Polls http://data.gdeltproject.org/gdeltv2/lastupdate.txt every 60s. When a
new 15-minute batch appears, downloads the Events and GKG CSVs (Mentions is
skipped by default — large and mostly redundant), parses them, bulk-inserts
into TimescaleDB hypertables, then updates the singleton gdelt_state row.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.gdelt_ingestor.downloader import (
    BatchManifest,
    download_csv,
    fetch_manifest,
)
from services.gdelt_ingestor.parser import parse_events, parse_gkg
from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("gdelt_ingestor")

POLL_INTERVAL = int(os.environ.get("GDELT_POLL_INTERVAL_SECONDS", "60"))
INGEST_EVENTS = os.environ.get("INGEST_EVENTS", "true").lower() == "true"
INGEST_GKG = os.environ.get("INGEST_GKG", "true").lower() == "true"
INGEST_MENTIONS = os.environ.get("INGEST_MENTIONS", "false").lower() == "true"
BATCH_ROWS = int(os.environ.get("GDELT_BATCH_ROWS", "2000"))
USER_AGENT = "HERALD/0.1 gdelt-ingestor"


EVENT_INSERT = text(
    """
    INSERT INTO gdelt_events (
        event_id, event_date, actor1_name, actor1_country, actor1_type,
        actor2_name, actor2_country, actor2_type,
        cameo_code, cameo_root_code, cameo_label,
        goldstein_scale, num_mentions, num_sources, num_articles, avg_tone,
        geo_fullname, geo_country, geo_lat, geo_lon, source_url
    ) VALUES (
        :event_id, :event_date, :actor1_name, :actor1_country, :actor1_type,
        :actor2_name, :actor2_country, :actor2_type,
        :cameo_code, :cameo_root_code, :cameo_label,
        :goldstein_scale, :num_mentions, :num_sources, :num_articles, :avg_tone,
        :geo_fullname, :geo_country, :geo_lat, :geo_lon, :source_url
    )
    ON CONFLICT (event_id, event_date) DO NOTHING
    """
)


GKG_INSERT = text(
    """
    INSERT INTO gdelt_gkg (
        record_id, record_date, source_url, source_name,
        themes, locations, persons, organizations,
        tone, positive_score, negative_score, polarity, activity_density,
        word_count, gcam_scores
    ) VALUES (
        :record_id, :record_date, :source_url, :source_name,
        :themes, :locations, :persons, :organizations,
        :tone, :positive_score, :negative_score, :polarity, :activity_density,
        :word_count, CAST(:gcam_scores AS JSONB)
    )
    ON CONFLICT (record_id, record_date) DO NOTHING
    """
)


async def batched_insert(
    engine: AsyncEngine, stmt, rows: Iterable[dict[str, Any]]
) -> int:
    """Execute an INSERT in chunks of BATCH_ROWS, return total rows processed."""
    total = 0
    buf: list[dict[str, Any]] = []
    for row in rows:
        buf.append(row)
        if len(buf) >= BATCH_ROWS:
            async with engine.begin() as conn:
                await conn.execute(stmt, buf)
            total += len(buf)
            buf.clear()
    if buf:
        async with engine.begin() as conn:
            await conn.execute(stmt, buf)
        total += len(buf)
    return total


async def get_last_batch_ts(engine: AsyncEngine) -> datetime | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT last_batch_ts FROM gdelt_state WHERE id=1"))
        ).first()
    return row[0] if row and row[0] else None


async def update_state(
    engine: AsyncEngine, batch_ts: datetime, events_n: int, gkg_n: int
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE gdelt_state SET
                    last_batch_ts = :ts,
                    last_run_at = NOW(),
                    last_events_count = :e,
                    last_gkg_count = :g
                WHERE id = 1
                """
            ),
            {"ts": batch_ts, "e": events_n, "g": gkg_n},
        )


async def process_batch(
    engine: AsyncEngine, client: httpx.AsyncClient, manifest: BatchManifest
) -> tuple[int, int]:
    events_n = 0
    gkg_n = 0

    if INGEST_EVENTS and manifest.events_url:
        log.info("downloading_events", url=manifest.events_url)
        csv_text = await download_csv(client, manifest.events_url)
        events_n = await batched_insert(engine, EVENT_INSERT, parse_events(csv_text))
        log.info("ingested_events", rows=events_n, batch_ts=manifest.batch_ts.isoformat())

    if INGEST_GKG and manifest.gkg_url:
        log.info("downloading_gkg", url=manifest.gkg_url)
        csv_text = await download_csv(client, manifest.gkg_url)
        # GCAM scores in the parser output are Python dicts — the SQL expects
        # JSON strings for CAST(... AS JSONB), so stringify at the boundary.
        def _gkg_rows():
            for row in parse_gkg(csv_text):
                row = dict(row)
                row["gcam_scores"] = json.dumps(row["gcam_scores"])
                yield row

        gkg_n = await batched_insert(engine, GKG_INSERT, _gkg_rows())
        log.info("ingested_gkg", rows=gkg_n, batch_ts=manifest.batch_ts.isoformat())

    if INGEST_MENTIONS:
        log.warning("mentions_ingest_requested_but_not_implemented")

    return events_n, gkg_n


async def main() -> None:
    log.info(
        "ingestor_starting",
        poll=POLL_INTERVAL,
        events=INGEST_EVENTS,
        gkg=INGEST_GKG,
        mentions=INGEST_MENTIONS,
        timescale_url=settings.timescale_url.split("@")[-1],
    )

    engine = create_async_engine(settings.timescale_url, pool_size=2, max_overflow=2)

    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        log.info("shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        while not stop.is_set():
            try:
                manifest = await fetch_manifest(client)
            except Exception as e:  # noqa: BLE001
                log.exception("manifest_fetch_failed", error=str(e))
                await asyncio.sleep(POLL_INTERVAL)
                continue

            last_ts = await get_last_batch_ts(engine)
            if last_ts is not None and manifest.batch_ts <= last_ts:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            log.info(
                "new_batch",
                batch_ts=manifest.batch_ts.isoformat(),
                last_ts=last_ts.isoformat() if last_ts else None,
            )
            try:
                events_n, gkg_n = await process_batch(engine, client, manifest)
                await update_state(engine, manifest.batch_ts, events_n, gkg_n)
            except Exception as e:  # noqa: BLE001
                log.exception(
                    "batch_processing_failed",
                    batch_ts=manifest.batch_ts.isoformat(),
                    error=str(e),
                )
            await asyncio.sleep(POLL_INTERVAL)

    await engine.dispose()
    log.info("ingestor_stopped")


if __name__ == "__main__":
    # Touch utcnow once so pylint/IDE don't mark it unused.
    _ = datetime.now(tz=timezone.utc)
    asyncio.run(main())
