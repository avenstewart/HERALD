"""Shared httpx client for the GDELT DOC 2.0 API.

Combines two protections against upstream rate-limiting:

* ``RedisTokenBucket`` — cross-process throttle enforced before each request,
  so multiple MCP workers cannot collectively exceed the configured rate.
  Uses a *reservation* model: each caller atomically decrements the token
  count (which may go negative) and receives a unique wait duration.  This
  eliminates thundering-herd wake-ups that occur with non-reserving buckets.
* ``RetryTransport`` — httpx transport that transparently retries 429 and 5xx
  responses with exponential backoff, honoring ``Retry-After`` when present.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time

import httpx
from redis.asyncio import Redis

from shared.logging import configure_logging
from shared.settings import settings

log = configure_logging("gdelt_client")

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_LIMITER_KEY = "herald:gdelt_doc:bucket"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# ── Lua scripts ──────────────────────────────────────────────────────────────

# Reservation-based token bucket.  Tokens can go negative, guaranteeing each
# caller a unique wait time and preventing thundering-herd retries.
_BUCKET_LUA = """
local key        = KEYS[1]
local rate       = tonumber(ARGV[1])
local capacity   = tonumber(ARGV[2])
local now_ms     = tonumber(ARGV[3])
local requested  = tonumber(ARGV[4])

local data  = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last   = tonumber(data[2])
if tokens == nil then
    tokens = capacity
    last   = now_ms
end

local elapsed_s = math.max(0, now_ms - last) / 1000.0
tokens = math.min(capacity, tokens + elapsed_s * rate)

-- Reserve slot: consume tokens, possibly going negative.
tokens = tokens - requested

local wait_ms = 0
if tokens < 0 then
    wait_ms = math.ceil((-tokens / rate) * 1000)
end

-- TTL must cover the longest possible outstanding reservation.
local ttl_ms = math.max(1000, wait_ms * 2 + math.ceil((capacity / rate) * 2000))
redis.call('HSET', key, 'tokens', tokens, 'last_refill', now_ms)
redis.call('PEXPIRE', key, ttl_ms)
return wait_ms
"""

# Return reserved tokens when a caller decides not to wait (max_wait exceeded).
_UNRESERVE_LUA = """
local key        = KEYS[1]
local capacity   = tonumber(ARGV[1])
local to_return  = tonumber(ARGV[2])
local tokens     = tonumber(redis.call('HGET', key, 'tokens'))
if tokens ~= nil then
    redis.call('HSET', key, 'tokens', math.min(capacity, tokens + to_return))
end
return 0
"""


# ── exceptions ───────────────────────────────────────────────────────────────


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds the maximum wait time for rate-limit tokens.

    ``retry_after`` is the seconds the caller would have had to wait for a
    token at the moment the reservation was rejected — useful for surfacing
    a precise backoff hint back to the client.
    """

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = float(retry_after)


# ── token bucket ─────────────────────────────────────────────────────────────


class RedisTokenBucket:
    """Distributed reservation-based token bucket keyed in Redis."""

    def __init__(
        self,
        redis: Redis,
        key: str,
        rate_per_sec: float,
        capacity: int,
        max_wait: float = 0,
    ):
        self._redis = redis
        self._key = key
        self._rate = float(rate_per_sec)
        self._capacity = int(capacity)
        self._max_wait = float(max_wait)
        self._acquire_script = redis.register_script(_BUCKET_LUA)
        self._unreserve_script = redis.register_script(_UNRESERVE_LUA)

    async def acquire(self, tokens: int = 1) -> None:
        """Reserve *tokens* from the bucket, blocking until the slot is due.

        Because each caller atomically reserves a slot (the Lua script allows
        the token count to go negative), every caller receives a unique wait
        duration — no thundering-herd retries.

        Raises ``RateLimitExceeded`` if the computed wait exceeds *max_wait*.
        """
        now_ms = int(time.time() * 1000)
        wait_ms = await self._acquire_script(
            keys=[self._key],
            args=[self._rate, self._capacity, now_ms, tokens],
        )
        wait = int(wait_ms) / 1000.0

        if wait <= 0:
            return

        # Check bounded wait before sleeping.
        if self._max_wait > 0 and wait > self._max_wait:
            await self._unreserve_script(
                keys=[self._key],
                args=[self._capacity, tokens],
            )
            log.warning(
                "gdelt_rate_limit_rejected",
                wait_s=round(wait, 1),
                max_wait_s=self._max_wait,
            )
            raise RateLimitExceeded(
                f"GDELT DOC API rate limit: would need to wait {wait:.1f}s "
                f"but max_wait is {self._max_wait:.0f}s",
                retry_after=wait,
            )

        log.debug("gdelt_rate_limit_wait", wait_s=round(wait, 1))
        # Small jitter avoids exact-same-instant arrivals after sleeping.
        await asyncio.sleep(wait + random.uniform(0, 0.15))


# ── retry transport ──────────────────────────────────────────────────────────


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
                if response.status_code in _RETRY_STATUSES:
                    log.warning(
                        "gdelt_retries_exhausted",
                        status=response.status_code,
                        attempts=attempt + 1,
                    )
                return response

            delay = min(
                self._max_delay,
                self._base_delay * (2**attempt) + random.uniform(0, 0.5),
            )
            retry_after = response.headers.get("retry-after")
            if retry_after is not None:
                with contextlib.suppress(ValueError):
                    delay = max(delay, float(retry_after))

            await response.aread()
            await response.aclose()
            last_response = response
            log.info(
                "gdelt_retry",
                status=response.status_code,
                attempt=attempt + 1,
                delay_s=round(delay, 1),
            )
            await asyncio.sleep(min(delay, self._max_delay))

        # Defensive: loop always returns inside — but keep typing happy.
        assert last_response is not None
        return last_response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


# ── client ───────────────────────────────────────────────────────────────────


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
            max_wait=settings.gdelt_doc_max_wait,
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
