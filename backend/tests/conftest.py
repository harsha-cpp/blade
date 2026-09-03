from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SCAN_PROCESS_POOL_ENABLED", "false")


@pytest.fixture(scope="session")
def app() -> FastAPI:
    from blade_backend.main import create_app  # noqa: PLC0415

    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
