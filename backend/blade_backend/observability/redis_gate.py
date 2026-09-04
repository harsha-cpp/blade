from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import cast

import structlog
from redis.asyncio import Redis

from blade_backend.settings import get_settings

logger = structlog.get_logger(__name__)

REDIS_FIRST_RETRY_SECONDS = 5.0
REDIS_MAX_RETRY_SECONDS = 300.0
REDIS_TIMEOUT_SECONDS = 0.05


class RedisGate:
    def __init__(self) -> None:
        self._client: Redis | None = None
        self._retry_at = 0.0
        self._backoff_seconds = REDIS_FIRST_RETRY_SECONDS

    async def client(self) -> Redis | None:
        if self._client is not None:
            return self._client
        if time.monotonic() < self._retry_at:
            return None
        try:
            candidate = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
                get_settings().redis_url,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
                decode_responses=True,
            )
            ping = cast(
                Callable[[], Awaitable[object]],
                candidate.ping,  # pyright: ignore[reportUnknownMemberType]
            )
            await ping()
        except Exception:
            self._schedule_retry()
            return None
        self._client = candidate
        self._backoff_seconds = REDIS_FIRST_RETRY_SECONDS
        return candidate

    def drop(self) -> None:
        self._client = None
        self._schedule_retry()
        logger.warning(
            "redis dropped out of the request path",
            retry_in_seconds=self._backoff_seconds,
            impact=(
                "shared counters fall back to per-process memory until redis answers "
                "again, so every rate limit and lockout is enforced per worker"
            ),
        )

    def suspend(self) -> None:
        self._client = None
        self._retry_at = float("inf")

    def _schedule_retry(self) -> None:
        self._retry_at = time.monotonic() + self._backoff_seconds
        self._backoff_seconds = min(self._backoff_seconds * 2, REDIS_MAX_RETRY_SECONDS)


async def hit_counter(redis: Redis, key: str, ttl_seconds: int) -> int:
    hits = int(await redis.incr(key))
    if hits == 1:
        await redis.expire(key, ttl_seconds)
    return hits
