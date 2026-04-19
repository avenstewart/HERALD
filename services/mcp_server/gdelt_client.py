"""Shared httpx client for the GDELT DOC 2.0 API.

Combines two protections against upstream rate-limiting:

* ``RetryTransport`` — httpx transport that transparently retries 429 and 5xx
  responses with exponential backoff, honoring ``Retry-After`` when present.
* ``RedisTokenBucket`` — cross-process throttle enforced before each request,
  so multiple MCP workers cannot collectively exceed the configured rate.
"""

from __future__ import annotations

import asyncio
import random
import time

import httpx
from redis.asyncio import Redis

from shared.settings import settings

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_LIMITER_KEY = "herald:gdelt_doc:bucket"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Atomic token-bucket: refills at `rate` tokens/sec up to `capacity`, consumes
# `requested` tokens if available, otherwise returns the ms the caller should
# wait before the bucket will have enough.
_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
    tokens = capacity
    last = now_ms
end

local elapsed_s = math.max(0, now_ms - last) / 1000.0
tokens = math.min(capacity, tokens + elapsed_s * rate)

local wait_ms = 0
if tokens >= requested then
    tokens = tokens - requested
else
    wait_ms = math.ceil(((requested - tokens) / rate) * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'last_refill', now_ms)
redis.call('PEXPIRE', key, math.max(1000, math.ceil((capacity / rate) * 2000)))
return wait_ms
"""


class RedisTokenBucket:
    """Distributed token bucket keyed in Redis."""

    def __init__(self, redis: Redis, key: str, rate_per_sec: float, capacity: int):
        self._redis = redis
        self._key = key
        self._rate = float(rate_per_sec)
        self._capacity = int(capacity)
        self._script = redis.register_script(_BUCKET_LUA)

    async def acquire(self, tokens: int = 1) -> None:
        """Block until `tokens` can be taken from the bucket."""
        while True:
            now_ms = int(time.time() * 1000)
            wait_ms = await self._script(
                keys=[self._key],
                args=[self._rate, self._capacity, now_ms, tokens],
            )
            wait = int(wait_ms) / 1000.0
            if wait <= 0:
                return
            # jitter avoids a thundering herd of waiters waking simultaneously
            await asyncio.sleep(wait + random.uniform(0, 0.1))


class RetryTransport(httpx.AsyncBaseTransport):
    """Retries 429 / 5xx responses with exponential backoff + Retry-After."""

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        max_retries: int,
        base_delay: float,
        max_delay: float,
    ):
        self._wrapped = wrapped
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            response = await self._wrapped.handle_async_request(request)
            if response.status_code not in _RETRY_STATUSES or attempt == self._max_retries:
                return response

            delay = min(
                self._max_delay,
                self._base_delay * (2**attempt) + random.uniform(0, 0.5),
            )
            retry_after = response.headers.get("retry-after")
            if retry_after is not None:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass  # HTTP-date form — ignore, stick with exponential

            await response.aread()
            await response.aclose()
            last_response = response
            await asyncio.sleep(min(delay, self._max_delay))

        # Defensive: loop always returns inside — but keep typing happy.
        assert last_response is not None
        return last_response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


class GdeltDocClient:
    """Singleton-ish client wrapping rate-limit + retry for the DOC API."""

    def __init__(self) -> None:
        base = httpx.AsyncHTTPTransport()
        transport = RetryTransport(
            base,
            max_retries=settings.gdelt_doc_max_retries,
            base_delay=settings.gdelt_doc_retry_base_delay,
            max_delay=settings.gdelt_doc_retry_max_delay,
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=settings.gdelt_doc_timeout,
        )
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._limiter = RedisTokenBucket(
            self._redis,
            key=_LIMITER_KEY,
            rate_per_sec=settings.gdelt_doc_rate_per_sec,
            capacity=settings.gdelt_doc_burst,
        )

    async def get(self, url: str, params: dict[str, str]) -> httpx.Response:
        await self._limiter.acquire()
        return await self._client.get(url, params=params)

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._redis.aclose()


_client: GdeltDocClient | None = None


def get_doc_client() -> GdeltDocClient:
    global _client
    if _client is None:
        _client = GdeltDocClient()
    return _client


async def close_doc_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
