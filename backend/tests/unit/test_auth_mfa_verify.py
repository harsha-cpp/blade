# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pyotp
import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from blade_backend.auth import mfa_api
from blade_backend.auth.mfa import (
    decode_challenge_token,
    encrypt_secret,
    generate_secret,
    issue_challenge_token,
)
from blade_backend.auth.mfa_api import MfaVerifyRequest, verify_mfa
from blade_backend.auth.rate_limit import MFA_MAX_ATTEMPTS, MfaChallengeGuard
from blade_backend.db.models import MemberMfa, Organization, OrgMember


class _FakeSession:
    def __init__(self, member: OrgMember, org: Organization, mfa: MemberMfa) -> None:
        self.member = member
        self.org = org
        self.mfa = mfa
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model: object, _id: object) -> object | None:
        if model is OrgMember:
            return self.member
        if model is Organization:
            return self.org
        return None

    async def scalar(self, statement: object) -> object | None:
        return self.mfa if "member_mfa" in str(statement) else None

    async def execute(self, _statement: object, _params: object = None) -> None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.commits += 1


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(Any, session))


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/mfa/verify",
            "query_string": b"",
            "headers": [],
            "client": ("203.0.113.10", 4444),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.fixture(name="guard")
def isolated_guard(monkeypatch: pytest.MonkeyPatch) -> MfaChallengeGuard:
    guard = MfaChallengeGuard()
    guard._redis.suspend()
    monkeypatch.setattr(mfa_api, "mfa_challenge_guard", guard)
    return guard


def _enrolled() -> tuple[str, _FakeSession, OrgMember, Organization]:
    org = Organization(
        id=uuid4(), name="City Hospital", slug="city-hospital", org_code="BLD-X", plan="free"
    )
    member = OrgMember(
        id=uuid4(),
        org_id=org.id,
        email="dr@hospital.in",
        name="Dr Sharma",
        role="owner",
        status="active",
    )
    secret = generate_secret()
    mfa = MemberMfa(
        id=uuid4(),
        member_id=member.id,
        secret_encrypted=encrypt_secret(secret),
        confirmed_at=datetime.now(UTC),
        backup_codes=[],
    )
    return secret, _FakeSession(member, org, mfa), member, org


@pytest.mark.unit
async def test_a_challenge_cannot_buy_a_second_session(guard: MfaChallengeGuard) -> None:
    secret, session, member, org = _enrolled()
    challenge_token = issue_challenge_token(member.id, org.id)
    payload = MfaVerifyRequest(challenge_token=challenge_token, code=pyotp.TOTP(secret).now())

    first = await verify_mfa(payload, Response(), _request(), _as_session(session))
    assert first.access_token

    replay = _FakeSession(member, org, session.mfa)
    with pytest.raises(HTTPException) as exc_info:
        await verify_mfa(payload, Response(), _request(), _as_session(replay))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "mfa challenge already used"
    assert await guard.is_spent(decode_challenge_token(challenge_token).challenge_id) is True


@pytest.mark.unit
async def test_wrong_codes_lock_the_member_out_and_burn_the_challenge(
    guard: MfaChallengeGuard,
) -> None:
    secret, session, member, org = _enrolled()
    challenge_token = issue_challenge_token(member.id, org.id)
    wrong = MfaVerifyRequest(challenge_token=challenge_token, code="000000")

    refusals: list[int] = []
    for _ in range(MFA_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as exc_info:
            await verify_mfa(wrong, Response(), _request(), _as_session(session))
        refusals.append(exc_info.value.status_code)

    assert refusals == [401] * (MFA_MAX_ATTEMPTS - 1) + [429]
    assert await guard.is_locked_out(member.id) is True

    fresh = MfaVerifyRequest(
        challenge_token=issue_challenge_token(member.id, org.id),
        code=pyotp.TOTP(secret).now(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await verify_mfa(fresh, Response(), _request(), _as_session(session))

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "too many mfa attempts"


@pytest.mark.unit
async def test_a_correct_code_clears_earlier_failures(guard: MfaChallengeGuard) -> None:
    secret, session, member, org = _enrolled()
    wrong = MfaVerifyRequest(
        challenge_token=issue_challenge_token(member.id, org.id), code="000000"
    )
    for _ in range(MFA_MAX_ATTEMPTS - 1):
        with pytest.raises(HTTPException):
            await verify_mfa(wrong, Response(), _request(), _as_session(session))

    good = MfaVerifyRequest(
        challenge_token=issue_challenge_token(member.id, org.id),
        code=pyotp.TOTP(secret).now(),
    )
    result = await verify_mfa(good, Response(), _request(), _as_session(session))

    assert result.access_token
    assert await guard.is_locked_out(member.id) is False
