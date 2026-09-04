# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest
from starlette.requests import Request

from blade_backend.auth.rate_limit import MAX_TRACKED_KEYS, LoginRateLimiter
from blade_backend.observability.redis_gate import (
    REDIS_FIRST_RETRY_SECONDS,
    REDIS_MAX_RETRY_SECONDS,
    RedisGate,
)


def _request(client: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "query_string": b"",
            "headers": [],
            "client": (client, 4444),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _limiter() -> LoginRateLimiter:
    limiter = LoginRateLimiter()
    limiter._redis.suspend()
    return limiter


@pytest.mark.unit
async def test_login_rate_limiter_blocks_after_minute_limit() -> None:
    limiter = _limiter()

    results = [await limiter.check(_request(), "person@example.com") for _ in range(6)]

    assert [result.allowed for result in results[:5]] == [True, True, True, True, True]
    assert results[5].allowed is False
    assert results[5].retry_after_seconds > 0


@pytest.mark.unit
async def test_login_rate_limiter_keys_by_ip_and_email() -> None:
    limiter = _limiter()

    for _ in range(5):
        assert (await limiter.check(_request(), "person@example.com")).allowed

    assert (await limiter.check(_request(), "other@example.com")).allowed


@pytest.mark.unit
async def test_spraying_many_emails_from_one_ip_runs_out_of_budget() -> None:
    limiter = _limiter()

    outcomes = [
        (await limiter.check(_request(), f"person-{index}@example.com")).allowed
        for index in range(150)
    ]

    assert outcomes.count(True) == 100
    assert outcomes[-1] is False


@pytest.mark.unit
async def test_one_email_sprayed_from_many_ips_runs_out_of_budget() -> None:
    limiter = _limiter()

    outcomes = [
        (await limiter.check(_request(f"198.51.100.{index}"), "victim@example.com")).allowed
        for index in range(40)
    ]

    assert outcomes.count(True) == 15
    assert outcomes[-1] is False


@pytest.mark.unit
async def test_only_the_first_refusal_in_a_window_asks_for_an_audit_row() -> None:
    limiter = _limiter()
    for _ in range(5):
        await limiter.check(_request(), "person@example.com")

    refusals = [await limiter.check(_request(), "person@example.com") for _ in range(3)]

    assert [refusal.allowed for refusal in refusals] == [False, False, False]
    assert [refusal.audit_due for refusal in refusals] == [True, False, False]


@pytest.mark.unit
async def test_memory_fallback_is_bounded() -> None:
    limiter = _limiter()

    for index in range(MAX_TRACKED_KEYS):
        await limiter.check(_request(f"10.0.{index // 256}.{index % 256}"), f"p{index}@x.test")

    assert limiter.tracked_keys() <= MAX_TRACKED_KEYS


@pytest.mark.unit
async def test_redis_is_retried_rather_than_disabled_for_the_process_lifetime() -> None:
    gate = RedisGate()
    gate.drop()
    assert await gate.client() is None

    gate._retry_at = 0.0

    assert gate._backoff_seconds > REDIS_FIRST_RETRY_SECONDS


@pytest.mark.unit
async def test_backoff_is_capped() -> None:
    gate = RedisGate()
    for _ in range(50):
        gate.drop()

    assert gate._backoff_seconds == REDIS_MAX_RETRY_SECONDS
