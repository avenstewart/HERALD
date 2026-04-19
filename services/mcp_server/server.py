"""HERALD MCP server entrypoint.

Exposes article query tools to consuming agents over MCP.
GDELT tools (events / themes / tone timelines / DOC API proxy) are scheduled
for the next release and will register through the same FastMCP instance.
"""

from __future__ import annotations

import asyncio
import signal

from fastmcp import FastMCP

from services.mcp_server.db import close_pool
from services.mcp_server.gdelt_client import close_doc_client
from services.mcp_server.tools import articles, gdelt
from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("mcp_server")

mcp = FastMCP(
    name="herald",
    instructions=(
        "HERALD provides two complementary streams: (1) a curated article corpus "
        "from RSS sources (see `search_articles`, `get_recent_articles`), and "
        "(2) structured GDELT v2 events + Global Knowledge Graph data (see "
        "`get_gdelt_events`, `get_gdelt_themes`, `get_gdelt_tone_timeline`, "
        "`get_gdelt_entities`, `gdelt_doc_search`). "
        "Use `get_ingestion_status` to verify pipeline health."
    ),
)

articles.register(mcp)
gdelt.register(mcp)


async def _shutdown() -> None:
    await close_doc_client()
    await close_pool()


def main() -> None:
    log.info(
        "mcp_starting",
        host=settings.mcp_host,
        port=settings.mcp_port,
        transport=settings.mcp_transport,
    )
    try:
        if settings.mcp_transport == "stdio":
            mcp.run(transport="stdio")
        else:
            mcp.run(
                transport="streamable-http",
                host=settings.mcp_host,
                port=settings.mcp_port,
            )
    finally:
        try:
            asyncio.run(_shutdown())
        except RuntimeError:
            pass


if __name__ == "__main__":
    # Allow Ctrl-C to terminate cleanly under tini.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: None)
    main()
