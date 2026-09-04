# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from blade_backend.auth import api as auth_api
from blade_backend.auth.api import ForgotPasswordRequest, forgot_password
from blade_backend.auth.rate_limit import LoginRateLimiter
from blade_backend.db.models import OrgMember, PasswordResetToken
from blade_backend.email import set_mailer

KNOWN = "dr@hospital.in"
UNKNOWN = "nobody@hospital.in"


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        del subject, body
        self.sent.append(to)


class _FakeSession:
    def __init__(self, member: OrgMember | None) -> None:
        self.member = member
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.member

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
            "path": "/api/v1/auth/forgot-password",
            "query_string": b"",
            "headers": [],
            "client": (f"198.51.100.{uuid4().int % 250}", 4444),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _member() -> OrgMember:
    return OrgMember(
        id=uuid4(),
        org_id=uuid4(),
        email=KNOWN,
        role="owner",
        status="active",
        created_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True, name="_isolated_limiter")
def isolated_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter = LoginRateLimiter()
    limiter._redis.suspend()
    monkeypatch.setattr(auth_api, "login_rate_limiter", limiter)
    yield
    set_mailer(None)


@pytest.mark.unit
async def test_a_known_address_defers_the_mail_to_a_background_task() -> None:
    mailer = _RecordingMailer()
    set_mailer(mailer)
    session = _FakeSession(_member())
    background = BackgroundTasks()

    response = await forgot_password(
        ForgotPasswordRequest(email=KNOWN), _request(), background, _as_session(session)
    )

    assert response.status_code == 204
    assert mailer.sent == []
    assert len(background.tasks) == 1
    assert [type(added) for added in session.added] == [PasswordResetToken]
    assert session.commits == 1

    await background()
    assert mailer.sent == [KNOWN]


@pytest.mark.unit
async def test_an_unknown_address_answers_the_same_and_queues_nothing() -> None:
    mailer = _RecordingMailer()
    set_mailer(mailer)
    session = _FakeSession(None)
    background = BackgroundTasks()

    response = await forgot_password(
        ForgotPasswordRequest(email=UNKNOWN), _request(), background, _as_session(session)
    )

    assert response.status_code == 204
    assert background.tasks == []
    assert session.added == []
    assert session.commits == 0

    await background()
    assert mailer.sent == []


@pytest.mark.unit
async def test_both_branches_mint_a_token_before_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minted: list[str] = []
    original = auth_api.issue_token

    def counting_issue_token(prefix: str, **kwargs: object) -> object:
        minted.append(prefix)
        return original(prefix, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(auth_api, "issue_token", counting_issue_token)
    set_mailer(_RecordingMailer())

    await forgot_password(
        ForgotPasswordRequest(email=KNOWN),
        _request(),
        BackgroundTasks(),
        _as_session(_FakeSession(_member())),
    )
    await forgot_password(
        ForgotPasswordRequest(email=UNKNOWN),
        _request(),
        BackgroundTasks(),
        _as_session(_FakeSession(None)),
    )

    assert minted == ["bladereset", "bladereset"]
