from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.passwords import hash_password
from blade_backend.db.models import AuditEvent, Organization, OrgMember
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"


async def _signup(client: AsyncClient) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:10]
    email = f"owner-{suffix}@auditedhospital.in"
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Audited Hospital {suffix}",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["organization"]["id"], email


async def _actions(session: AsyncSession, org_id: str) -> list[str]:
    return list(
        (
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.org_id == uuid.UUID(org_id))
                .order_by(AuditEvent.created_at.asc())
            )
        ).all()
    )


async def _metadata(session: AsyncSession, org_id: str, action: str) -> dict[str, object]:
    row = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == uuid.UUID(org_id), AuditEvent.action == action
        )
    )
    assert row is not None, f"no audit row for {action}"
    return row.metadata_json


async def test_domain_lifecycle_is_audited(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, org_id, _email = await _signup(api_client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await api_client.post(
        "/api/v1/domains",
        headers=headers,
        json={"domain": "partnerclinic.in", "classification": "partner"},
    )
    assert created.status_code == 201, created.text
    domain_id = created.json()["id"]

    updated = await api_client.patch(
        f"/api/v1/domains/{domain_id}",
        headers=headers,
        json={"classification": "blocked"},
    )
    assert updated.status_code == 200, updated.text

    deleted = await api_client.delete(f"/api/v1/domains/{domain_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    actions = await _actions(db_session, org_id)
    assert {"domain_added", "domain_updated", "domain_removed"} <= set(actions)

    change = await _metadata(db_session, org_id, "domain_updated")
    assert change["before"] == {
        "direction": "both",
        "classification": "partner",
        "notes": None,
    }
    assert change["after"] == {
        "direction": "both",
        "classification": "blocked",
        "notes": None,
    }


async def test_renaming_the_org_is_audited(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, org_id, _email = await _signup(api_client)
    previous = await db_session.scalar(
        select(Organization.name).where(Organization.id == uuid.UUID(org_id))
    )

    renamed = await api_client.patch(
        "/api/v1/orgs/current",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Renamed Hospital"},
    )
    assert renamed.status_code == 200, renamed.text

    change = await _metadata(db_session, org_id, "org_renamed")
    assert change["from_name"] == previous
    assert change["to_name"] == "Renamed Hospital"


async def test_a_role_change_records_both_sides(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, org_id, _email = await _signup(api_client)
    target = OrgMember(
        org_id=uuid.UUID(org_id),
        email=f"analyst-{uuid.uuid4().hex[:10]}@auditedhospital.in",
        password_hash=hash_password(PASSWORD),
        role="viewer",
        status="active",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(target)
    await db_session.commit()

    changed = await api_client.patch(
        f"/api/v1/members/{target.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "analyst"},
    )
    assert changed.status_code == 200, changed.text

    change = await _metadata(db_session, org_id, "member_role_changed")
    assert change["from_role"] == "viewer"
    assert change["to_role"] == "analyst"
    assert change["member_id"] == str(target.id)


async def test_successful_and_failed_logins_are_audited(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    _token, org_id, email = await _signup(api_client)
    org_slug = await db_session.scalar(
        select(Organization.slug).where(Organization.id == uuid.UUID(org_id))
    )

    good = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "org_slug": org_slug},
    )
    assert good.status_code == 200, good.text
    assert good.headers["cache-control"] == "no-store"

    bad = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "not-the-right-password", "org_slug": org_slug},
    )
    assert bad.status_code == 401

    actions = await _actions(db_session, org_id)
    assert "login_succeeded" in actions
    assert "login_failed" in actions

    failure = await _metadata(db_session, org_id, "login_failed")
    assert failure["email"] == email
    assert PASSWORD not in str(failure)
