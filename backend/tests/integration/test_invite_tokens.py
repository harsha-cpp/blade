from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.tokens import SHA256_HASH_PREFIX
from blade_backend.db.models import Organization, OrgMember
from blade_backend.email import set_mailer
from blade_backend.members.api import INVITE_TTL_DAYS
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"
_INVITE_LINK = re.compile(r"/accept-invite\?token=([A-Za-z0-9_-]+)")


class _RecordingMailer:
    def __init__(self) -> None:
        self.bodies: list[str] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        del to, subject
        self.bodies.append(body)

    def last_invite_token(self) -> str:
        match = _INVITE_LINK.search(self.bodies[-1])
        assert match is not None, self.bodies[-1]
        return match.group(1)


@pytest.fixture(name="mailer")
def recording_mailer() -> Iterator[_RecordingMailer]:
    mailer = _RecordingMailer()
    set_mailer(mailer)
    yield mailer
    set_mailer(None)


async def _signup(client: AsyncClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Invite Hospital {suffix}",
            "email": f"owner-{suffix}@invitehospital.in",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["organization"]["id"]


async def _invite(client: AsyncClient, token: str, email: str) -> str:
    response = await client.post(
        "/api/v1/members/invite",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": email, "role": "analyst"},
    )
    assert response.status_code == 201, response.text
    return response.json()["member"]["id"]


async def test_the_stored_invite_is_a_hash_and_the_emailed_token_still_works(
    api_client: AsyncClient,
    db_session: AsyncSession,
    mailer: _RecordingMailer,
) -> None:
    owner_token, org_id = await _signup(api_client)
    email = f"invitee-{uuid.uuid4().hex[:10]}@invitehospital.in"
    member_id = await _invite(api_client, owner_token, email)
    raw_token = mailer.last_invite_token()

    stored = await db_session.scalar(
        select(OrgMember.invite_token).where(OrgMember.id == uuid.UUID(member_id))
    )
    assert stored is not None
    assert stored.startswith(SHA256_HASH_PREFIX)
    assert raw_token not in stored

    org_slug = await db_session.scalar(
        select(Organization.slug).where(Organization.id == uuid.UUID(org_id))
    )
    accepted = await api_client.post(
        "/api/v1/members/accept-invite",
        json={"invite_token": raw_token, "password": PASSWORD, "name": "Dr Invitee"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["org_slug"] == org_slug
    assert accepted.json()["member"]["status"] == "active"

    replayed = await api_client.post(
        "/api/v1/members/accept-invite",
        json={"invite_token": raw_token, "password": PASSWORD},
    )
    assert replayed.status_code == 404


async def test_the_hash_read_out_of_the_table_is_not_an_invitation(
    api_client: AsyncClient,
    db_session: AsyncSession,
    mailer: _RecordingMailer,
) -> None:
    owner_token, _org_id = await _signup(api_client)
    email = f"invitee-{uuid.uuid4().hex[:10]}@invitehospital.in"
    member_id = await _invite(api_client, owner_token, email)
    assert mailer.bodies

    stored = await db_session.scalar(
        select(OrgMember.invite_token).where(OrgMember.id == uuid.UUID(member_id))
    )
    assert stored is not None

    response = await api_client.post(
        "/api/v1/members/accept-invite",
        json={"invite_token": stored, "password": PASSWORD},
    )

    assert response.status_code == 404


async def test_a_legacy_plaintext_invite_is_accepted_once(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _owner_token, org_id = await _signup(api_client)
    legacy_token = f"legacy-{uuid.uuid4().hex}"
    legacy = OrgMember(
        org_id=uuid.UUID(org_id),
        email=f"legacy-{uuid.uuid4().hex[:10]}@invitehospital.in",
        role="viewer",
        status="invited",
        invite_token=legacy_token,
        invite_expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    db_session.add(legacy)
    await db_session.commit()

    accepted = await api_client.post(
        "/api/v1/members/accept-invite",
        json={"invite_token": legacy_token, "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text

    replayed = await api_client.post(
        "/api/v1/members/accept-invite",
        json={"invite_token": legacy_token, "password": PASSWORD},
    )
    assert replayed.status_code == 404

    cleared = await db_session.scalar(
        select(OrgMember.invite_token).where(OrgMember.id == legacy.id)
    )
    assert cleared is None
