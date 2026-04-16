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
from services.mcp_server.tools import articles
from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("mcp_server")

mcp = FastMCP(
    name="herald",
    instructions=(
        "HERALD provides full-text search and queries over a curated, "
        "self-hosted news article corpus. Use `search_articles` for keyword "
        "queries, `get_recent_articles` for time-windowed feeds, and "
        "`get_ingestion_status` to verify the pipeline is healthy."
    ),
)

articles.register(mcp)


async def _shutdown() -> None:
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
