"""Helpers shared across services: URL normalization, hashing, date parsing."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as dateparser

TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_", "fbclid", "gclid", "mc_", "_hs")
TRACKING_PARAMS_EXACT: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "msclkid",
        "igshid",
        "yclid",
        "ref",
        "ref_src",
        "ref_url",
        "_hsenc",
        "_hsmi",
    }
)


def _strip_tracking(query: str) -> str:
    kept = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k not in TRACKING_PARAMS_EXACT
        and not any(k.startswith(p) for p in TRACKING_PARAM_PREFIXES)
    ]
    return urlencode(kept)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for deduplication.

    - lowercases scheme + host
    - strips default ports
    - strips fragments
    - strips common tracking params (utm_*, fbclid, ...)
    - collapses trailing slash on non-root paths
    """
    if not url:
        return url
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "http"
    host = parts.hostname or ""
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = _strip_tracking(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(title: str | None, body: str | None) -> str:
    """Stable fingerprint of article content: SHA256(title + first 500 chars of body)."""
    t = (title or "").strip()
    b = (body or "").strip()[:500]
    return sha256_hex(f"{t}:{b}")


def parse_date(value: str | datetime | None) -> datetime | None:
    """Best-effort parse of a published-at date string into a tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    value = str(value).strip()
    if not value:
        return None
    try:
        dt = dateparser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def domain_of(url: str) -> str:
    """Extract hostname (no port, no www.) from a URL. Empty string on failure."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
