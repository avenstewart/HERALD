"""Download and unzip GDELT v2 batch files.

GDELT publishes every 15 minutes, with three files per batch (events, mentions,
GKG) listed in http://data.gdeltproject.org/gdeltv2/lastupdate.txt. Each file
is a zip archive containing a single tab-delimited CSV.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

LAST_UPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"


@dataclass(slots=True)
class BatchManifest:
    batch_ts: datetime
    events_url: str | None
    mentions_url: str | None
    gkg_url: str | None


def _ts_from_url(url: str) -> datetime:
    """Extract the YYYYMMDDHHMMSS timestamp from a GDELT filename."""
    # e.g. http://data.gdeltproject.org/gdeltv2/20260416021500.export.CSV.zip
    base = url.rsplit("/", 1)[-1]
    ts = base.split(".", 1)[0]
    return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _classify(url: str) -> str | None:
    lower = url.lower()
    if ".export.csv.zip" in lower:
        return "events"
    if ".mentions.csv.zip" in lower:
        return "mentions"
    if ".gkg.csv.zip" in lower:
        return "gkg"
    return None


async def fetch_manifest(client: httpx.AsyncClient) -> BatchManifest:
    """Parse GDELT's lastupdate.txt into a BatchManifest."""
    resp = await client.get(LAST_UPDATE_URL, timeout=20)
    resp.raise_for_status()
    urls: dict[str, str] = {}
    batch_ts: datetime | None = None
    for line in resp.text.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        url = parts[-1]
        kind = _classify(url)
        if kind is None:
            continue
        urls[kind] = url
        if batch_ts is None:
            batch_ts = _ts_from_url(url)
    if batch_ts is None:
        raise ValueError("No valid GDELT urls in lastupdate.txt")
    return BatchManifest(
        batch_ts=batch_ts,
        events_url=urls.get("events"),
        mentions_url=urls.get("mentions"),
        gkg_url=urls.get("gkg"),
    )


async def download_csv(client: httpx.AsyncClient, url: str) -> str:
    """Download a GDELT zip file and return the extracted CSV as text."""
    resp = await client.get(url, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        # Each archive has exactly one .CSV member.
        members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"No CSV in {url}")
        with zf.open(members[0]) as f:
            return f.read().decode("utf-8", errors="replace")
