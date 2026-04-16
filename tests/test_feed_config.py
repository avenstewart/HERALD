from __future__ import annotations

from pathlib import Path

import pytest

from services.feed_poller.config import TIER_INTERVALS, Source, load_sources


def test_tier_intervals():
    assert TIER_INTERVALS["A"] == 120
    assert TIER_INTERVALS["B"] == 300
    assert TIER_INTERVALS["C"] == 900


def test_resolve_url_direct():
    s = Source(name="x", tier="B", url="https://example.com/feed")
    assert s.resolve_url("http://rsshub:1200") == "https://example.com/feed"


def test_resolve_url_rsshub_route():
    s = Source(name="x", tier="A", rsshub_route="reuters/world")
    assert s.resolve_url("http://rsshub:1200") == "http://rsshub:1200/reuters/world"


def test_resolve_url_requires_one():
    s = Source(name="x", tier="A")
    with pytest.raises(ValueError):
        s.resolve_url("http://rsshub:1200")


def test_load_seed_feeds_yaml():
    """The shipped sources/feeds.yaml must parse cleanly."""
    path = Path(__file__).resolve().parent.parent / "sources" / "feeds.yaml"
    sources = load_sources(path)
    assert len(sources) >= 5
    names = {s.name for s in sources}
    assert "BBC World Service" in names
    for s in sources:
        assert s.url or s.rsshub_route
        assert s.interval_seconds > 0
