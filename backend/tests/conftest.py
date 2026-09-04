from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis

DEFAULT_TEST_REDIS_URL = "redis://localhost:6379/9"
TEST_REDIS_URL = os.environ.get("BLADE_TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)

os.environ.setdefault("SCAN_PROCESS_POOL_ENABLED", "false")
os.environ["REDIS_URL"] = TEST_REDIS_URL


@pytest.fixture(scope="session", autouse=True, name="_isolated_redis_database")
def isolated_redis_database() -> None:
    try:
        client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            TEST_REDIS_URL,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        flush = cast(
            Callable[[], object],
            client.flushdb,  # pyright: ignore[reportUnknownMemberType]
        )
        flush()
        client.close()
    except Exception:
        return


@pytest.fixture(scope="session")
def app() -> FastAPI:
    from blade_backend.main import create_app  # noqa: PLC0415

    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
