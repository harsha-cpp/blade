from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from blade_backend.auth.rate_limit import AccessRequestLimiter
from blade_backend.main import create_app
from blade_backend.public import api as public_api
from blade_backend.settings import Settings, get_settings
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

DASHBOARD_ORIGIN = "https://app.tryblade.example"
LANDING_ORIGIN = "https://tryblade.example"
UNLISTED_ORIGIN = "https://not-invited.example"
ACCESS_REQUEST_PATH = "/api/v1/public/access-requests"


@pytest.fixture(autouse=True, name="_isolated_access_request_limiter")
def isolated_access_request_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter = AccessRequestLimiter()
    limiter._redis.suspend()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(public_api, "access_request_limiter", limiter)
    yield


def _app() -> FastAPI:
    return create_app(Settings(cors_origins=[DASHBOARD_ORIGIN], landing_origins=[LANDING_ORIGIN]))


@pytest.fixture(name="cors_client")
async def cors_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _oversized_body() -> str:
    return json.dumps({"filler": "x" * (get_settings().api_max_request_bytes + 4096)})


def _access_request(email: str) -> dict[str, object]:
    return {
        "organization": "City General Hospital",
        "name": "Asha Rao",
        "email": email,
        "message": "We would like a pilot.",
    }


async def test_a_rejected_dashboard_request_still_carries_cors_headers(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.get("/api/v1/audit", headers={"origin": DASHBOARD_ORIGIN})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_an_oversized_dashboard_request_still_carries_cors_headers(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.post(
        "/api/v1/scan/email",
        content=_oversized_body(),
        headers={"origin": DASHBOARD_ORIGIN, "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_a_throttled_dashboard_request_still_carries_cors_headers(
    cors_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()

    responses = [
        await cors_client.get("/api/v1/audit", headers={"origin": DASHBOARD_ORIGIN})
        for _ in range(3)
    ]

    throttled = responses[-1]
    assert throttled.status_code == 429
    assert throttled.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN
    assert throttled.headers["access-control-allow-credentials"] == "true"


async def test_the_landing_origin_is_answered_on_its_two_public_paths(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.post(
        ACCESS_REQUEST_PATH,
        json=_access_request(f"lead-{uuid.uuid4().hex[:12]}@citygeneral.example"),
        headers={"origin": LANDING_ORIGIN},
    )

    assert response.status_code == 202
    assert response.headers["access-control-allow-origin"] == LANDING_ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["vary"] == "Origin"


async def test_an_oversized_landing_request_still_carries_cors_headers(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.post(
        ACCESS_REQUEST_PATH,
        content=_oversized_body(),
        headers={"origin": LANDING_ORIGIN, "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == LANDING_ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["vary"] == "Origin"


async def test_the_landing_preflight_survives_the_credentialed_policy(
    cors_client: AsyncClient,
) -> None:
    response = await cors_client.options(
        ACCESS_REQUEST_PATH,
        headers={
            "origin": LANDING_ORIGIN,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LANDING_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


async def test_an_unlisted_origin_is_answered_without_cors_headers(
    cors_client: AsyncClient,
) -> None:
    unauthorized = await cors_client.get("/api/v1/audit", headers={"origin": UNLISTED_ORIGIN})
    oversized = await cors_client.post(
        ACCESS_REQUEST_PATH,
        content=_oversized_body(),
        headers={"origin": UNLISTED_ORIGIN, "content-type": "application/json"},
    )

    assert unauthorized.status_code == 401
    assert "access-control-allow-origin" not in unauthorized.headers
    assert oversized.status_code == 413
    assert "access-control-allow-origin" not in oversized.headers
