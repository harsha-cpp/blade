from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.passwords import hash_password
from blade_backend.db.models import AuditEvent, DeviceToken, Organization, OrgMember
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"


async def _signup(client: AsyncClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Leaver Hospital {suffix}",
            "email": f"owner-{suffix}@leaverhospital.in",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["organization"]["id"]


async def _add_active_member(session: AsyncSession, org_id: str, email: str) -> str:
    member = OrgMember(
        org_id=uuid.UUID(org_id),
        email=email,
        name="Dr Leaver",
        password_hash=hash_password(PASSWORD),
        role="analyst",
        status="active",
        email_verified_at=datetime.now(UTC),
    )
    session.add(member)
    await session.commit()
    return str(member.id)


async def _login(client: AsyncClient, email: str, org_slug: str) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "org_slug": org_slug},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _enroll_device(client: AsyncClient, access_token: str) -> str:
    response = await client.post(
        "/api/v1/devices/enroll",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"label": "Leaver laptop"},
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()["device_token"]


async def _scan(client: AsyncClient, device_token: str) -> int:
    response = await client.post(
        "/api/v1/scan/email",
        headers={"X-Blade-Device-Token": device_token},
        json={
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Rota",
            "body": "Nothing sensitive here.",
            "recipients": [],
        },
    )
    return response.status_code


async def test_removing_a_member_kills_their_device_token(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_token, org_id = await _signup(api_client)
    org_slug = await db_session.scalar(
        select(Organization.slug).where(Organization.id == uuid.UUID(org_id))
    )
    assert org_slug is not None
    email = f"leaver-{uuid.uuid4().hex[:10]}@leaverhospital.in"
    member_id = await _add_active_member(db_session, org_id, email)

    device_token = await _enroll_device(api_client, await _login(api_client, email, org_slug))
    assert await _scan(api_client, device_token) == 200

    removed = await api_client.delete(
        f"/api/v1/members/{member_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert removed.status_code == 204, removed.text

    assert await _scan(api_client, device_token) == 401

    devices = (
        await db_session.scalars(select(DeviceToken).where(DeviceToken.org_id == uuid.UUID(org_id)))
    ).all()
    assert [device.revoked_at is not None for device in devices] == [True]

    actions = set(
        (
            await db_session.scalars(
                select(AuditEvent.action).where(AuditEvent.org_id == uuid.UUID(org_id))
            )
        ).all()
    )
    assert {"member_removed", "devices_revoked_with_member"} <= actions
