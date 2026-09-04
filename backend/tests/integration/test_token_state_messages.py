from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.tokens import (
    EMAIL_VERIFY_PREFIX,
    PASSWORD_RESET_PREFIX,
    IssuedToken,
    issue_token,
)
from blade_backend.db.models import EmailVerificationToken, OrgMember, PasswordResetToken
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"
NEW_PASSWORD = "another-correct-horse-battery-7"


async def _signup(client: AsyncClient) -> str:
    suffix = uuid.uuid4().hex[:10]
    email = f"owner-{suffix}@neutralhospital.in"
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Neutral Hospital {suffix}",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return email


async def _member_id(session: AsyncSession, email: str) -> uuid.UUID:
    member_id = await session.scalar(select(OrgMember.id).where(OrgMember.email == email))
    assert member_id is not None
    return member_id


def _issued(prefix: str, *, ttl_seconds: int) -> IssuedToken:
    return issue_token(prefix, ttl=timedelta(seconds=ttl_seconds))


async def test_reset_token_states_share_one_message(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = await _signup(api_client)
    member_id = await _member_id(db_session, email)
    now = datetime.now(UTC)
    spent = _issued(PASSWORD_RESET_PREFIX, ttl_seconds=3600)
    expired = _issued(PASSWORD_RESET_PREFIX, ttl_seconds=-3600)
    db_session.add(
        PasswordResetToken(
            id=spent.id,
            member_id=member_id,
            token_hash=spent.token_hash,
            expires_at=spent.expires_at,
            used_at=now,
        )
    )
    db_session.add(
        PasswordResetToken(
            id=expired.id,
            member_id=member_id,
            token_hash=expired.token_hash,
            expires_at=expired.expires_at,
        )
    )
    await db_session.commit()

    async def _detail(token: str) -> str:
        response = await api_client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "password": NEW_PASSWORD},
        )
        assert response.status_code == 400, response.text
        return str(response.json()["detail"])

    used_detail = await _detail(spent.raw_token)
    expired_detail = await _detail(expired.raw_token)
    unknown_detail = await _detail(
        _issued(PASSWORD_RESET_PREFIX, ttl_seconds=3600).raw_token,
    )
    malformed_detail = await _detail("bladereset_not-a-valid-token-shape")

    assert used_detail == expired_detail == unknown_detail == malformed_detail
    assert used_detail == "reset link is invalid or has expired"


async def test_verification_token_states_share_one_message(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    email = await _signup(api_client)
    member_id = await _member_id(db_session, email)
    now = datetime.now(UTC)
    spent = _issued(EMAIL_VERIFY_PREFIX, ttl_seconds=3600)
    expired = _issued(EMAIL_VERIFY_PREFIX, ttl_seconds=-3600)
    db_session.add(
        EmailVerificationToken(
            id=spent.id,
            member_id=member_id,
            token_hash=spent.token_hash,
            expires_at=spent.expires_at,
            used_at=now,
        )
    )
    db_session.add(
        EmailVerificationToken(
            id=expired.id,
            member_id=member_id,
            token_hash=expired.token_hash,
            expires_at=expired.expires_at,
        )
    )
    await db_session.commit()

    async def _detail(token: str) -> str:
        response = await api_client.post("/api/v1/auth/verify-email", json={"token": token})
        assert response.status_code == 400, response.text
        return str(response.json()["detail"])

    used_detail = await _detail(spent.raw_token)
    expired_detail = await _detail(expired.raw_token)
    unknown_detail = await _detail(_issued(EMAIL_VERIFY_PREFIX, ttl_seconds=3600).raw_token)
    malformed_detail = await _detail("bladeverify_not-a-valid-token-shape")

    assert used_detail == expired_detail == unknown_detail == malformed_detail
    assert used_detail == "verification link is invalid or has expired"
