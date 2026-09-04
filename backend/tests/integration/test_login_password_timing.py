from __future__ import annotations

import uuid
from collections.abc import Iterator
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth import api as auth_api
from blade_backend.auth.rate_limit import LoginRateLimiter
from blade_backend.db.models import AuditEvent
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"
ABSENT_HASH = auth_api._ABSENT_MEMBER_PASSWORD_HASH  # pyright: ignore[reportPrivateUsage]


@pytest.fixture(autouse=True, name="_isolated_login_limiter")
def isolated_login_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter = LoginRateLimiter()
    limiter._redis.suspend()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(auth_api, "login_rate_limiter", limiter)
    yield


@pytest.fixture(name="verified_hashes")
def verified_hashes(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    seen: list[bytes] = []
    real = auth_api.verify_password

    def _recording(plain: str, hashed: bytes) -> bool:
        seen.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(auth_api, "verify_password", _recording)
    return seen


async def _signup(client: AsyncClient) -> tuple[str, UUID]:
    suffix = uuid.uuid4().hex[:10]
    email = f"owner-{suffix}@timinghospital.in"
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Timing Hospital {suffix}",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return email, UUID(response.json()["organization"]["id"])


async def test_an_unknown_address_costs_the_same_verification_as_a_wrong_password(
    api_client: AsyncClient, verified_hashes: list[bytes]
) -> None:
    email, _org_id = await _signup(api_client)
    verified_hashes.clear()

    unknown = await api_client.post(
        "/api/v1/auth/login",
        json={"email": f"nobody-{uuid.uuid4().hex[:10]}@timinghospital.in", "password": PASSWORD},
    )

    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "invalid credentials"
    assert verified_hashes == [ABSENT_HASH]

    verified_hashes.clear()
    wrong = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": f"wrong-{PASSWORD}"},
    )

    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "invalid credentials"
    assert len(verified_hashes) == 1
    assert verified_hashes[0] != ABSENT_HASH


async def test_repeated_failures_write_one_audit_row_per_window(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    email, org_id = await _signup(api_client)

    for _ in range(3):
        refused = await api_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": f"wrong-{PASSWORD}"},
        )
        assert refused.status_code == 401

    written = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "login_failed",
        )
    )

    assert written == 1
