"""Extractor happy-path test — Trafilatura is mocked so the test stays
hermetic and doesn't hit the network."""

from __future__ import annotations

import json

import pytest

from services.extractor import extract as extract_mod


@pytest.mark.asyncio
async def test_extract_returns_structured_article(monkeypatch):
    sample_html = "<html><body><article>Hello world body content here.</article></body></html>"
    sample_json = json.dumps(
        {
            "title": "Sample Headline",
            "text": "Hello world body content here.",
            "author": "Jane Doe",
            "date": "2026-04-15",
            "language": "en",
        }
    )

    monkeypatch.setattr(extract_mod.trafilatura, "fetch_url", lambda url: sample_html)
    monkeypatch.setattr(extract_mod.trafilatura, "extract", lambda *a, **kw: sample_json)

    article = await extract_mod.extract("https://example.com/x")
    assert article is not None
    assert article.title == "Sample Headline"
    assert article.author == "Jane Doe"
    assert article.word_count == 5
    assert article.language == "en"
    assert len(article.content_hash) == 64
    assert article.published_at is not None


@pytest.mark.asyncio
async def test_extract_returns_none_on_empty_fetch(monkeypatch):
    monkeypatch.setattr(extract_mod.trafilatura, "fetch_url", lambda url: None)
    assert await extract_mod.extract("https://example.com/x") is None


@pytest.mark.asyncio
async def test_extract_returns_none_on_empty_extraction(monkeypatch):
    monkeypatch.setattr(extract_mod.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(extract_mod.trafilatura, "extract", lambda *a, **kw: None)
    assert await extract_mod.extract("https://example.com/x") is None
