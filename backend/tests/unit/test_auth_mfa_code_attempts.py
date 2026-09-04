# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pyotp
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth import mfa_api
from blade_backend.auth.mfa import encrypt_secret, generate_secret
from blade_backend.auth.mfa_api import CodeRequest, confirm_mfa, disable_mfa
from blade_backend.auth.rate_limit import MFA_MAX_ATTEMPTS, MfaChallengeGuard
from blade_backend.db.models import MemberMfa
from blade_backend.deps import Principal

WRONG_CODE = "000000"


class _FakeSession:
    def __init__(self, mfa: MemberMfa) -> None:
        self.mfa = mfa
        self.deleted: list[object] = []
        self.commits = 0

    async def scalar(self, statement: object) -> object | None:
        return self.mfa if "member_mfa" in str(statement) else None

    async def execute(self, _statement: object, _params: object = None) -> None:
        return None

    def add(self, _item: object) -> None:
        return None

    async def delete(self, item: object) -> None:
        self.deleted.append(item)

    async def commit(self) -> None:
        self.commits += 1


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(Any, session))


@pytest.fixture(name="guard")
def isolated_guard(monkeypatch: pytest.MonkeyPatch) -> MfaChallengeGuard:
    guard = MfaChallengeGuard()
    guard._redis.suspend()
    monkeypatch.setattr(mfa_api, "mfa_challenge_guard", guard)
    return guard


def _principal() -> Principal:
    return Principal(member_id=uuid4(), org_id=uuid4(), email="dr@hospital.in", role="owner")


def _enrolment(member: Principal, *, confirmed: bool) -> tuple[str, _FakeSession]:
    secret = generate_secret()
    record = MemberMfa(
        id=uuid4(),
        member_id=member.member_id,
        secret_encrypted=encrypt_secret(secret),
        confirmed_at=datetime.now(UTC) if confirmed else None,
        backup_codes=[],
    )
    return secret, _FakeSession(record)


@pytest.mark.unit
async def test_confirm_locks_out_after_the_shared_budget_of_wrong_codes(
    guard: MfaChallengeGuard,
) -> None:
    member = _principal()
    _secret, session = _enrolment(member, confirmed=False)

    for _ in range(MFA_MAX_ATTEMPTS - 1):
        with pytest.raises(HTTPException) as rejected:
            await confirm_mfa(CodeRequest(code=WRONG_CODE), member, _as_session(session))
        assert rejected.value.status_code == 400

    with pytest.raises(HTTPException) as locked:
        await confirm_mfa(CodeRequest(code=WRONG_CODE), member, _as_session(session))

    assert locked.value.status_code == 429
    assert await guard.is_locked_out(member.member_id) is True


@pytest.mark.unit
async def test_a_locked_out_member_cannot_confirm_even_with_the_right_code(
    guard: MfaChallengeGuard,
) -> None:
    member = _principal()
    secret, session = _enrolment(member, confirmed=False)
    for _ in range(MFA_MAX_ATTEMPTS):
        await guard.record_failure(member.member_id)

    with pytest.raises(HTTPException) as locked:
        await confirm_mfa(CodeRequest(code=pyotp.TOTP(secret).now()), member, _as_session(session))

    assert locked.value.status_code == 429


@pytest.mark.unit
async def test_a_good_confirmation_clears_the_failure_count(guard: MfaChallengeGuard) -> None:
    member = _principal()
    secret, session = _enrolment(member, confirmed=False)
    await guard.record_failure(member.member_id)

    confirmed = await confirm_mfa(
        CodeRequest(code=pyotp.TOTP(secret).now()), member, _as_session(session)
    )

    assert confirmed.backup_codes
    assert await guard.is_locked_out(member.member_id) is False


@pytest.mark.unit
async def test_disable_counts_wrong_codes_against_the_same_budget(
    guard: MfaChallengeGuard,
) -> None:
    member = _principal()
    _secret, session = _enrolment(member, confirmed=True)

    for _ in range(MFA_MAX_ATTEMPTS - 1):
        with pytest.raises(HTTPException) as rejected:
            await disable_mfa(CodeRequest(code=WRONG_CODE), member, _as_session(session))
        assert rejected.value.status_code == 400

    with pytest.raises(HTTPException) as locked:
        await disable_mfa(CodeRequest(code=WRONG_CODE), member, _as_session(session))

    assert locked.value.status_code == 429
    assert session.deleted == []


@pytest.mark.unit
async def test_disable_with_a_good_code_still_removes_the_enrolment(
    guard: MfaChallengeGuard,
) -> None:
    member = _principal()
    secret, session = _enrolment(member, confirmed=True)
    await guard.record_failure(member.member_id)

    response = await disable_mfa(
        CodeRequest(code=pyotp.TOTP(secret).now()), member, _as_session(session)
    )

    assert response.status_code == 204
    assert session.deleted == [session.mfa]
    assert await guard.is_locked_out(member.member_id) is False
