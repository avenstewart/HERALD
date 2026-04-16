"""Dedup logic — exercised with an in-memory fake Redis so the tests stay
hermetic and fast."""

from __future__ import annotations

import pytest

from services.feed_poller.dedup import filter_new_urls, mark_seen
from shared.settings import settings


class FakePipeline:
    def __init__(self, store: "FakeRedis"):
        self._store = store
        self._ops: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def sadd(self, key, *vals):
        self._ops.append(("sadd", key, vals))

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))

    async def execute(self):
        for op in self._ops:
            if op[0] == "sadd":
                _, key, vals = op
                self._store.sets.setdefault(key, set()).update(vals)
            elif op[0] == "set":
                _, key, value, _ex = op
                self._store.kv[key] = value
        self._ops.clear()


class FakeRedis:
    def __init__(self):
        self.sets: dict[str, set[str]] = {}
        self.kv: dict[str, str] = {}

    async def smismember(self, key, members):
        s = self.sets.get(key, set())
        return [m in s for m in members]

    def pipeline(self, transaction=False):
        return FakePipeline(self)


@pytest.mark.asyncio
async def test_filter_new_urls_returns_all_when_empty():
    redis = FakeRedis()
    fresh = await filter_new_urls(redis, ["https://a.example.com/1", "https://b.example.com/2"])
    assert len(fresh) == 2
    assert all(isinstance(p, tuple) and len(p) == 2 for p in fresh)


@pytest.mark.asyncio
async def test_mark_then_filter_skips_seen():
    redis = FakeRedis()
    fresh = await filter_new_urls(redis, ["https://a.example.com/1"])
    await mark_seen(redis, fresh)
    again = await filter_new_urls(redis, ["https://a.example.com/1"])
    assert again == []
    assert settings.seen_urls_set in redis.sets


@pytest.mark.asyncio
async def test_filter_normalizes_before_check():
    """Two URLs that differ only by tracking params should be deduped."""
    redis = FakeRedis()
    fresh1 = await filter_new_urls(redis, ["https://a.example.com/x?utm_source=foo"])
    await mark_seen(redis, fresh1)
    fresh2 = await filter_new_urls(redis, ["https://a.example.com/x?utm_source=bar"])
    assert fresh2 == []
