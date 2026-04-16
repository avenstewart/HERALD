"""HERALD backfill CLI — Fundus-powered historical crawler.

Fundus ships pre-configured extractors for hundreds of named publishers. This
CLI drives two Fundus entry points:

    * Crawler         — live crawl of publishers' own websites.
    * CCNewsCrawler   — historical extraction from the CommonCrawl CC-NEWS
                        archive, going back years with no rate-limiting risk.

Articles are written to the same `articles` table the live pipeline populates,
distinguished by `extraction_method = 'fundus'` or `'fundus_ccnews'`.

Usage:
    herald-backfill list-publishers
    herald-backfill crawl --publishers us.APNews,uk.BBC --months 3
    herald-backfill ccnews --publishers us.APNews --start 2024-01-01 --end 2024-06-01
    herald-backfill status
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Annotated

import typer

from services.backfill.upsert import connect, upsert_article
from shared.logging import configure_logging

log = configure_logging("backfill")
app = typer.Typer(add_completion=False, help=__doc__)


def _resolve_publishers(dotted_names: list[str]):
    """Resolve 'region.Publisher' or 'Publisher' names from PublisherCollection.

    Fundus nests publishers under regional namespaces (PublisherCollection.us,
    .uk, .de, etc.). We accept either 'us.APNews' (preferred) or bare 'APNews'
    (searched across all regions).
    """
    try:
        from fundus import PublisherCollection
    except ImportError as e:
        raise RuntimeError(
            "Fundus is not installed. Install with: pip install fundus"
        ) from e

    resolved = []
    missing = []
    for dotted in dotted_names:
        parts = dotted.strip().split(".")
        pub = None
        if len(parts) == 2:
            region_name, pub_name = parts
            region = getattr(PublisherCollection, region_name, None)
            if region is not None:
                pub = getattr(region, pub_name, None)
        else:
            # Bare name: search all regions
            name = parts[0]
            for region_attr in dir(PublisherCollection):
                if region_attr.startswith("_"):
                    continue
                region = getattr(PublisherCollection, region_attr)
                cand = getattr(region, name, None)
                if cand is not None:
                    pub = cand
                    break
        if pub is None:
            missing.append(dotted)
        else:
            resolved.append(pub)

    if missing:
        raise typer.BadParameter(
            f"Publishers not found: {', '.join(missing)}. "
            f"Use `herald-backfill list-publishers` to see valid names."
        )
    return resolved


def _fundus_to_record(article, fallback_source_name: str) -> dict:
    """Normalize a Fundus Article object to our upsert_article kwargs."""
    title = getattr(article, "title", None) or None
    text = getattr(article, "plaintext", None) or None
    published = getattr(article, "publishing_date", None)
    authors = getattr(article, "authors", None)
    author = ", ".join(authors) if authors else None
    url = getattr(article, "url", None) or getattr(article, "html", {}).get("url")
    language = getattr(article, "lang", None) or "en"
    return {
        "url": url,
        "title": title,
        "content": text,
        "author": author,
        "published_at": published,
        "source_name": fallback_source_name,
        "language": language,
    }


@app.command("list-publishers")
def list_publishers() -> None:
    """List every publisher known to the installed Fundus version."""
    from fundus import PublisherCollection

    count = 0
    for region_attr in sorted(dir(PublisherCollection)):
        if region_attr.startswith("_"):
            continue
        region = getattr(PublisherCollection, region_attr)
        for pub_attr in sorted(dir(region)):
            if pub_attr.startswith("_"):
                continue
            typer.echo(f"{region_attr}.{pub_attr}")
            count += 1
    typer.echo(f"\n{count} publishers available.", err=True)


@app.command()
def crawl(
    publishers: Annotated[str, typer.Option(help="Comma-separated region.Publisher names")],
    months: Annotated[int, typer.Option(help="Max article age in months")] = 6,
    max_articles: Annotated[int, typer.Option(help="Hard cap on articles per run")] = 10_000,
) -> None:
    """Live-crawl publishers' websites for recent articles."""
    try:
        from fundus import Crawler
    except ImportError as e:
        raise typer.BadParameter(
            "Fundus not installed. See backfill Dockerfile dependencies."
        ) from e

    pub_list = _resolve_publishers([p for p in publishers.split(",") if p.strip()])
    log.info("crawl_starting", publishers=len(pub_list), months=months, cap=max_articles)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=months * 30)

    crawler = Crawler(*pub_list)
    conn = connect()
    stored = 0
    seen = 0
    try:
        for article in crawler.crawl(max_articles=max_articles):
            seen += 1
            pub_date = article.publishing_date
            if pub_date is not None:
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                if pub_date < cutoff:
                    continue
            record = _fundus_to_record(article, fallback_source_name=article.publisher)
            if upsert_article(conn, extraction_method="fundus", **record):
                stored += 1
            if stored % 100 == 0 and stored:
                log.info("crawl_progress", seen=seen, stored=stored)
    finally:
        conn.close()
    log.info("crawl_complete", seen=seen, stored=stored)


@app.command()
def ccnews(
    publishers: Annotated[str, typer.Option(help="Comma-separated region.Publisher names")],
    start: Annotated[str, typer.Option(help="ISO date, e.g. 2024-01-01")],
    end: Annotated[str, typer.Option(help="ISO date, e.g. 2024-06-01")],
    max_articles: Annotated[int, typer.Option(help="Hard cap on articles per run")] = 50_000,
) -> None:
    """Crawl the CC-NEWS CommonCrawl archive for historical articles."""
    try:
        from fundus import CCNewsCrawler
    except ImportError as e:
        raise typer.BadParameter("Fundus not installed.") from e

    pub_list = _resolve_publishers([p for p in publishers.split(",") if p.strip()])
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    log.info(
        "ccnews_starting",
        publishers=len(pub_list),
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
    )

    crawler = CCNewsCrawler(*pub_list, start=start_dt, end=end_dt)
    conn = connect()
    stored = 0
    seen = 0
    try:
        for article in crawler.crawl(max_articles=max_articles, only_complete=True):
            seen += 1
            record = _fundus_to_record(article, fallback_source_name=article.publisher)
            if upsert_article(conn, extraction_method="fundus_ccnews", **record):
                stored += 1
            if stored % 500 == 0 and stored:
                log.info("ccnews_progress", seen=seen, stored=stored)
    finally:
        conn.close()
    log.info("ccnews_complete", seen=seen, stored=stored)


@app.command()
def status() -> None:
    """Summarize article counts by extraction method."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT extraction_method, COUNT(*), MIN(published_at), MAX(published_at)
                FROM articles
                GROUP BY extraction_method
                ORDER BY COUNT(*) DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        typer.echo("No articles in DB.")
        return
    typer.echo(f"{'method':<18} {'count':>10}  {'earliest':<20} {'latest':<20}")
    for method, n, lo, hi in rows:
        typer.echo(f"{method:<18} {n:>10,}  {str(lo):<20} {str(hi):<20}")


def main() -> None:
    try:
        app()
    except Exception as e:  # noqa: BLE001
        log.exception("backfill_failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
