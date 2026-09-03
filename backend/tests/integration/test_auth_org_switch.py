from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

import pyotp
import pytest
from httpx import AsyncClient

from blade_backend.email.mailer import set_mailer
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"
ATTACKER_PASSWORD = "unrelated-donkey-lantern-fig-4"
_VERIFY_LINK = re.compile(r"/verify-email\?token=(\S+)")


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest.fixture(name="mailbox")
def recording_mailbox() -> Iterator[_RecordingMailer]:
    mailer = _RecordingMailer()
    set_mailer(mailer)
    yield mailer
    set_mailer(None)


def _unique_email() -> str:
    return f"shared-{uuid.uuid4().hex[:10]}@sunrisehospital.in"


async def _signup(
    client: AsyncClient, *, email: str, org_name: str, password: str = PASSWORD
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"{org_name} {uuid.uuid4().hex[:8]}",
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _verification_tokens(mailbox: _RecordingMailer, email: str) -> list[str]:
    tokens: list[str] = []
    for recipient, subject, body in mailbox.sent:
        if recipient != email or "Verify" not in subject:
            continue
        match = _VERIFY_LINK.search(body)
        assert match is not None, body
        tokens.append(unquote(match.group(1)))
    return tokens


async def _verify_email(client: AsyncClient, token: str) -> None:
    response = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert response.status_code == 204, response.text


def _headers(session_body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session_body['access_token']}"}


async def test_unverified_signup_cannot_reach_an_existing_org_with_the_same_email(
    api_client: AsyncClient, mailbox: _RecordingMailer
) -> None:
    email = _unique_email()
    victim = await _signup(api_client, email=email, org_name="Victim Hospital")
    await _verify_email(api_client, _verification_tokens(mailbox, email)[0])
    attacker = await _signup(
        api_client, email=email, org_name="Attacker Clinic", password=ATTACKER_PASSWORD
    )
    headers = _headers(attacker)

    listed = await api_client.get("/api/v1/auth/my-orgs", headers=headers)
    assert listed.status_code == 200, listed.text
    assert victim["organization"]["id"] not in {org["id"] for org in listed.json()}

    switched = await api_client.post(
        "/api/v1/auth/switch-org",
        headers=headers,
        json={"org_id": victim["organization"]["id"]},
    )
    assert switched.status_code == 403, switched.text


async def test_verified_member_of_two_orgs_switches_into_a_session(
    api_client: AsyncClient, mailbox: _RecordingMailer
) -> None:
    email = _unique_email()
    first = await _signup(api_client, email=email, org_name="Northside Hospital")
    second = await _signup(api_client, email=email, org_name="Southside Clinic")
    for token in _verification_tokens(mailbox, email):
        await _verify_email(api_client, token)

    headers = _headers(first)
    listed = await api_client.get("/api/v1/auth/my-orgs", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {org["id"] for org in listed.json()} >= {
        first["organization"]["id"],
        second["organization"]["id"],
    }

    switched = await api_client.post(
        "/api/v1/auth/switch-org",
        headers=headers,
        json={"org_id": second["organization"]["id"]},
    )
    assert switched.status_code == 200, switched.text
    body: dict[str, Any] = switched.json()
    assert body["access_token"]
    assert body["organization"]["id"] == second["organization"]["id"]
    assert body["mfa_required"] is False


async def test_switching_into_an_mfa_protected_membership_challenges_instead_of_signing_in(
    api_client: AsyncClient, mailbox: _RecordingMailer
) -> None:
    email = _unique_email()
    first = await _signup(api_client, email=email, org_name="Eastside Hospital")
    second = await _signup(api_client, email=email, org_name="Westside Clinic")
    for token in _verification_tokens(mailbox, email):
        await _verify_email(api_client, token)

    target_headers = _headers(second)
    enrolled = await api_client.post("/api/v1/auth/mfa/enroll", headers=target_headers)
    assert enrolled.status_code == 200, enrolled.text
    totp = pyotp.TOTP(enrolled.json()["secret"])
    confirmed = await api_client.post(
        "/api/v1/auth/mfa/confirm", headers=target_headers, json={"code": totp.now()}
    )
    assert confirmed.status_code == 200, confirmed.text

    switched = await api_client.post(
        "/api/v1/auth/switch-org",
        headers=_headers(first),
        json={"org_id": second["organization"]["id"]},
    )
    assert switched.status_code == 200, switched.text
    challenge: dict[str, Any] = switched.json()
    assert challenge["mfa_required"] is True
    assert challenge["challenge_token"]
    assert "access_token" not in challenge

    verified = await api_client.post(
        "/api/v1/auth/mfa/verify",
        json={"challenge_token": challenge["challenge_token"], "code": totp.now()},
    )
    assert verified.status_code == 200, verified.text
    session_body: dict[str, Any] = verified.json()
    assert session_body["access_token"]
    assert session_body["organization"]["id"] == second["organization"]["id"]
    assert session_body["member"]["mfa_enabled"] is True


async def test_verifying_one_membership_leaves_a_same_email_membership_unverified(
    api_client: AsyncClient, mailbox: _RecordingMailer
) -> None:
    email = _unique_email()
    victim = await _signup(api_client, email=email, org_name="Riverside Hospital")
    attacker = await _signup(
        api_client, email=email, org_name="Riverside Impostor", password=ATTACKER_PASSWORD
    )
    tokens = _verification_tokens(mailbox, email)
    assert len(tokens) == 2
    await _verify_email(api_client, tokens[0])

    victim_me = await api_client.get("/api/v1/auth/me", headers=_headers(victim))
    assert victim_me.status_code == 200, victim_me.text
    assert victim_me.json()["member"]["email_verified"] is True

    attacker_me = await api_client.get("/api/v1/auth/me", headers=_headers(attacker))
    assert attacker_me.status_code == 200, attacker_me.text
    assert attacker_me.json()["member"]["email_verified"] is False

    switched = await api_client.post(
        "/api/v1/auth/switch-org",
        headers=_headers(attacker),
        json={"org_id": victim["organization"]["id"]},
    )
    assert switched.status_code == 403, switched.text
