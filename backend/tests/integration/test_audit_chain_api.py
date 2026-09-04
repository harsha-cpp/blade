from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.passwords import hash_password
from blade_backend.db.models import MemberRole, OrgMember
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"


async def _signup(client: AsyncClient) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Verified Hospital {suffix}",
            "email": f"owner-{suffix}@verifiedhospital.in",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["organization"]["id"], body["organization"]["slug"]


async def _member_token(
    client: AsyncClient,
    session: AsyncSession,
    org_id: str,
    org_slug: str,
    role: MemberRole,
) -> str:
    email = f"{role}-{uuid.uuid4().hex[:10]}@verifiedhospital.in"
    session.add(
        OrgMember(
            org_id=uuid.UUID(org_id),
            email=email,
            name=f"Dr {role.title()}",
            password_hash=hash_password(PASSWORD),
            role=role,
            status="active",
            email_verified_at=datetime.now(UTC),
        )
    )
    await session.commit()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "org_slug": org_slug},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def test_an_owner_verifies_the_chain(api_client: AsyncClient) -> None:
    token, _org_id, _slug = await _signup(api_client)
    await api_client.post(
        "/api/v1/domains",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "verifiedhospital.in", "direction": "both", "classification": "internal"},
    )

    response = await api_client.get(
        "/api/v1/audit/chain", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["checked"] >= 1
    assert body["legacy"] == 0
    assert body["failed_check"] is None
    assert body["cached"] is False
    assert body["verified_at"]


async def test_an_admin_may_verify_and_an_analyst_may_not(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _owner_token, org_id, slug = await _signup(api_client)
    admin_token = await _member_token(api_client, db_session, org_id, slug, "admin")
    analyst_token = await _member_token(api_client, db_session, org_id, slug, "analyst")

    allowed = await api_client.get(
        "/api/v1/audit/chain", headers={"Authorization": f"Bearer {admin_token}"}
    )
    refused = await api_client.get(
        "/api/v1/audit/chain", headers={"Authorization": f"Bearer {analyst_token}"}
    )

    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 403


async def test_a_second_verification_inside_the_cooldown_is_served_from_cache(
    api_client: AsyncClient,
) -> None:
    token, _org_id, _slug = await _signup(api_client)

    first = await api_client.get(
        "/api/v1/audit/chain", headers={"Authorization": f"Bearer {token}"}
    )
    second = await api_client.get(
        "/api/v1/audit/chain", headers={"Authorization": f"Bearer {token}"}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["verified_at"] == first.json()["verified_at"]


async def test_the_audit_search_treats_wildcards_as_literal_text(api_client: AsyncClient) -> None:
    token, _org_id, _slug = await _signup(api_client)
    await api_client.post(
        "/api/v1/domains",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "verifiedhospital.in", "direction": "both", "classification": "internal"},
    )

    async def _search(needle: str) -> list[dict[str, object]]:
        response = await api_client.get(
            "/api/v1/audit",
            headers={"Authorization": f"Bearer {token}"},
            params={"search": needle},
        )
        assert response.status_code == 200, response.text
        events: list[dict[str, object]] = response.json()["events"]
        return events

    assert await _search("domain_added")
    assert await _search("%") == []
    assert await _search("domain%added") == []
    assert await _search("member_owner") == []
