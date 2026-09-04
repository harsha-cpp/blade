# pyright: reportPrivateUsage=false

from __future__ import annotations

import secrets

import pytest
from starlette.requests import Request

from blade_backend.auth.jwt import issue_access_token
from blade_backend.observability import security as security_module
from blade_backend.observability.security import RateLimitMiddleware, resolve_client_ip
from blade_backend.settings import Settings


def _request(
    path: str = "/api/v1/orgs/current",
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    client: str = "203.0.113.10",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": headers or [],
            "client": (client, 4444),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


async def _noop(_scope: object, _receive: object, _send: object) -> None:
    return None


def _middleware(**overrides: object) -> RateLimitMiddleware:
    middleware = RateLimitMiddleware(_noop, **overrides)  # type: ignore[arg-type]
    middleware._redis.suspend()
    return middleware


@pytest.mark.unit
def test_client_ip_falls_back_to_socket_when_no_proxies_are_trusted() -> None:
    request = _request(headers=[(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1")])

    assert resolve_client_ip(request, 0) == "203.0.113.10"


@pytest.mark.unit
def test_client_ip_reads_the_nth_entry_from_the_right() -> None:
    request = _request(headers=[(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1, 10.0.0.2")])

    assert resolve_client_ip(request, 1) == "10.0.0.2"
    assert resolve_client_ip(request, 2) == "10.0.0.1"


@pytest.mark.unit
def test_spoofed_short_forwarded_header_cannot_pick_the_client_ip() -> None:
    request = _request(headers=[(b"x-forwarded-for", b"1.2.3.4")])

    assert resolve_client_ip(request, 2) == "203.0.113.10"


@pytest.mark.unit
def test_missing_forwarded_header_falls_back() -> None:
    assert resolve_client_ip(_request(), 2) == "203.0.113.10"


@pytest.mark.unit
async def test_limit_is_enforced_per_key() -> None:
    middleware = _middleware(limit=3)

    allowed = [not await middleware._over_limit("ip:1.2.3.4") for _ in range(4)]

    assert allowed == [True, True, True, False]
    assert await middleware._over_limit("ip:5.6.7.8") is False


@pytest.mark.unit
def test_scan_and_events_are_exempt_by_default() -> None:
    middleware = _middleware()

    assert middleware._is_exempt("/api/v1/scan/text") is True
    assert middleware._is_exempt("/api/v1/events") is True
    assert middleware._is_exempt("/api/v1/orgs/current") is False


@pytest.mark.unit
async def test_memory_buckets_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    bounded = Settings(api_rate_limit_max_keys=100)
    monkeypatch.setattr(security_module, "get_settings", lambda: bounded)
    middleware = _middleware(limit=5)

    for index in range(500):
        await middleware._over_limit(f"ip:10.0.0.{index}")

    assert len(middleware._buckets) <= 100


@pytest.mark.unit
def test_a_verified_access_token_gets_its_own_bucket() -> None:
    middleware = _middleware()
    token = issue_access_token("11111111-1111-1111-1111-111111111111", "org-1", "owner")

    keyed = middleware._key(_request(headers=[(b"authorization", f"Bearer {token}".encode())]))

    assert keyed == "member:11111111-1111-1111-1111-111111111111"


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        [(b"authorization", b"Bearer not-a-real-token")],
        [(b"x-blade-device-token", b"bladedev_x.y")],
        [],
    ],
)
def test_unverified_credentials_are_keyed_on_the_client_ip(
    headers: list[tuple[bytes, bytes]],
) -> None:
    middleware = _middleware()

    assert middleware._key(_request(headers=headers)) == "ip:203.0.113.10"


@pytest.mark.unit
async def test_a_flood_of_random_bearer_tokens_is_limited_as_one_client() -> None:
    middleware = _middleware(limit=3)

    allowed = [
        not await middleware._over_limit(
            middleware._key(
                _request(
                    headers=[(b"authorization", f"Bearer {secrets.token_urlsafe(24)}".encode())]
                )
            )
        )
        for _ in range(6)
    ]

    assert allowed == [True, True, True, False, False, False]
    assert len(middleware._buckets) == 1


@pytest.mark.unit
async def test_a_flood_of_random_device_tokens_is_limited_as_one_client() -> None:
    middleware = _middleware(limit=3)

    allowed = [
        not await middleware._over_limit(
            middleware._key(
                _request(
                    headers=[
                        (b"x-blade-device-token", f"bladedev_{secrets.token_hex(8)}.x".encode())
                    ]
                )
            )
        )
        for _ in range(6)
    ]

    assert allowed == [True, True, True, False, False, False]
    assert len(middleware._buckets) == 1
