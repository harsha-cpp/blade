from __future__ import annotations

import socket

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from blade_backend import health as health_module
from blade_backend.health import (
    MALFORMED_REDIS_URL_DETAIL,
    ComponentStatus,
    ReadinessStatus,
    build_readiness_probe,
    redis_status,
)
from blade_backend.main import create_app
from blade_backend.settings import Settings

REDIS_HOST = "localhost"
REDIS_PORT = 6379
TEST_REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/9"


def _redis_reachable() -> bool:
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=0.5):
            return True
    except OSError:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_reachable(), reason=f"no Redis at {TEST_REDIS_URL}. Start one with `make dev-up`."
)


@pytest.fixture(name="_healthy_database")
def healthy_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr(health_module, "check_database", _ok)


@pytest.mark.unit
async def test_healthz(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.unit
async def test_readyz_returns_component_status() -> None:
    async def ready() -> ReadinessStatus:
        return ReadinessStatus(
            ready=True,
            components=(
                ComponentStatus(name="database", ok=True, detail="ok"),
                ComponentStatus(name="redis", ok=True, detail="reachable"),
            ),
        )

    app = create_app(readiness_probe=ready)
    async with AsyncClient(transport=client_transport(app), base_url="http://test") as ac:
        r = await ac.get("/readyz")

    assert r.status_code == 200
    assert r.json() == {
        "status": "ready",
        "degraded": False,
        "components": [
            {"name": "database", "ok": True, "detail": "ok"},
            {"name": "redis", "ok": True, "detail": "reachable"},
        ],
    }


def client_transport(app: FastAPI) -> ASGITransport:
    return ASGITransport(app=app)


@pytest.mark.unit
@pytest.mark.usefixtures("_healthy_database")
async def test_an_unreachable_redis_is_degraded_but_still_ready() -> None:
    probe = build_readiness_probe(Settings(redis_url="redis://127.0.0.1:1/0"))

    readiness = await probe()

    assert readiness.ready is True
    assert readiness.degraded is True
    assert [component.ok for component in readiness.components] == [True, False]


@pytest.mark.unit
@pytest.mark.usefixtures("_healthy_database")
async def test_a_malformed_redis_url_is_not_ready_rather_than_a_crash() -> None:
    probe = build_readiness_probe(Settings(redis_url="not-a-redis-url"))

    readiness = await probe()

    assert readiness.ready is False
    assert readiness.as_dict()["status"] == "not_ready"
    assert readiness.components[1].detail == MALFORMED_REDIS_URL_DETAIL


@pytest.mark.unit
@requires_redis
async def test_the_redis_probe_authenticates_rather_than_only_opening_a_socket() -> None:
    unauthenticated = await redis_status(
        f"redis://:wrong-password@{REDIS_HOST}:{REDIS_PORT}/9", timeout=2.0
    )

    assert unauthenticated.ok is False
    assert unauthenticated.required is False
    assert unauthenticated.detail != MALFORMED_REDIS_URL_DETAIL


@pytest.mark.unit
@requires_redis
async def test_a_reachable_redis_answers_the_ping() -> None:
    reachable = await redis_status(TEST_REDIS_URL, timeout=2.0)

    assert reachable.ok is True
    assert reachable.detail == "pong"
