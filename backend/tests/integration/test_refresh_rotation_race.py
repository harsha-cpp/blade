from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import RefreshToken
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]


async def test_two_tabs_refreshing_at_once_mint_exactly_one_child(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex[:10]
    signup = await api_client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Race Hospital {suffix}",
            "email": f"owner-{suffix}@racehospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert signup.status_code == 201, signup.text
    member_id = uuid.UUID(signup.json()["member"]["id"])

    first, second = await asyncio.gather(
        api_client.post("/api/v1/auth/refresh"),
        api_client.post("/api/v1/auth/refresh"),
    )

    assert {first.status_code, second.status_code} == {200}

    tokens = (
        await db_session.scalars(select(RefreshToken).where(RefreshToken.member_id == member_id))
    ).all()
    rotated = [token for token in tokens if token.rotated_at is not None]

    assert len(tokens) == 2
    assert len(rotated) == 1
    assert {token.family_id for token in tokens} == {rotated[0].family_id}

    children = await db_session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(
            RefreshToken.family_id == rotated[0].family_id,
            RefreshToken.rotated_at.is_(None),
        )
    )
    assert children == 1
