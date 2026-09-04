# pyright: reportPrivateUsage=false

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.rate_limit import ACCESS_REQUEST_PER_HOUR, AccessRequestLimiter
from blade_backend.db.models import AccessRequest, AuditEvent, OrgMember
from blade_backend.email import set_mailer
from blade_backend.public import api as public_api
from blade_backend.settings import get_settings
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"


@pytest.fixture(autouse=True, name="_isolated_access_request_limiter")
def isolated_access_request_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter = AccessRequestLimiter()
    limiter._redis.suspend()
    monkeypatch.setattr(public_api, "access_request_limiter", limiter)
    yield


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        del to, subject
        self.sent.append(body)


@pytest.fixture(name="operator_mailbox")
def operator_mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RecordingMailer]:
    mailer = _RecordingMailer()
    monkeypatch.setenv("ACCESS_REQUEST_NOTIFY_EMAIL", "ops@blade.example")
    get_settings.cache_clear()
    set_mailer(mailer)
    yield mailer
    set_mailer(None)
    get_settings.cache_clear()


def _payload(email: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "organization": "City General Hospital",
        "name": "Asha Rao",
        "email": email,
        "phone": None,
        "role": None,
        "size": None,
        "message": None,
        "website": None,
    }
    body.update(overrides)
    return body


def _address() -> str:
    return f"lead-{uuid.uuid4().hex[:12]}@citygeneral.example"


async def _rows_for(session: AsyncSession, email: str) -> list[AccessRequest]:
    return list(
        (await session.scalars(select(AccessRequest).where(AccessRequest.email == email))).all()
    )


async def _signup(client: AsyncClient) -> tuple[str, str, UUID]:
    suffix = uuid.uuid4().hex[:10]
    email = f"owner-{suffix}@operatorhospital.in"
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Operator Hospital {suffix}",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], email, UUID(body["organization"]["id"])


def _make_platform_admin(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", email)
    get_settings.cache_clear()


async def _verify_email(session: AsyncSession, email: str) -> None:
    await session.execute(
        update(OrgMember)
        .where(OrgMember.email == email)
        .values(email_verified_at=datetime.now(UTC))
    )
    await session.commit()


async def test_a_request_is_stored_and_acknowledged(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    email = _address()

    response = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(
            email,
            phone="+91 90000 00000",
            role="CISO",
            size="500-1000",
            message="We want a pilot.",
        ),
        headers={"user-agent": "Mozilla/5.0"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "received"}

    rows = await _rows_for(db_session, email)
    assert len(rows) == 1
    stored = rows[0]
    assert stored.organization == "City General Hospital"
    assert stored.size == "500-1000"
    assert stored.status == "new"
    assert stored.notes is None
    assert stored.handled_at is None
    assert stored.handled_by is None
    assert stored.user_agent == "Mozilla/5.0"
    assert stored.client_ip


async def test_a_filled_honeypot_stores_nothing_and_still_reads_as_accepted(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    email = _address()

    response = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(email, website="https://spam.example"),
    )

    assert response.status_code == 202
    assert response.json() == {"status": "received"}
    assert await _rows_for(db_session, email) == []


async def test_the_same_address_inside_a_day_updates_rather_than_duplicates(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    email = _address()
    first = await api_client.post(
        "/api/v1/public/access-requests", json=_payload(email, message="short")
    )
    assert first.status_code == 202

    longer = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(email, message="a much longer second description of the need"),
    )
    assert longer.status_code == 202

    shorter = await api_client.post(
        "/api/v1/public/access-requests", json=_payload(email, message="tiny")
    )
    assert shorter.status_code == 202

    rows = await _rows_for(db_session, email)
    assert len(rows) == 1
    assert rows[0].message == "a much longer second description of the need"


async def test_two_people_on_one_mailbox_each_get_a_row_and_a_notification(
    api_client: AsyncClient,
    db_session: AsyncSession,
    operator_mailbox: _RecordingMailer,
) -> None:
    shared = f"info-{uuid.uuid4().hex[:12]}@citygeneral.example"

    first = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(shared, name="Asha Rao", phone="+91 90000 00000"),
    )
    second = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(shared, name="Vikram Nair", phone="+91 90000 00001"),
    )

    assert first.status_code == 202
    assert second.status_code == 202

    rows = await _rows_for(db_session, shared)
    assert len(rows) == 2
    assert {row.name for row in rows} == {"Asha Rao", "Vikram Nair"}
    assert len(operator_mailbox.sent) == 2
    assert any("Vikram Nair" in body for body in operator_mailbox.sent)


async def test_a_resubmission_by_the_same_person_updates_in_place(
    api_client: AsyncClient,
    db_session: AsyncSession,
    operator_mailbox: _RecordingMailer,
) -> None:
    email = _address()
    person: dict[str, object] = {"name": " Asha   Rao ", "phone": "+91 90000 00000"}

    first = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(email, message="short", **person),
    )
    again = await api_client.post(
        "/api/v1/public/access-requests",
        json=_payload(
            email,
            message="a much longer second description of the need",
            organization="city general hospital",
            **person,
        ),
    )

    assert first.status_code == 202
    assert again.status_code == 202

    rows = await _rows_for(db_session, email)
    assert len(rows) == 1
    assert rows[0].message == "a much longer second description of the need"
    assert len(operator_mailbox.sent) == 1


async def test_one_address_burns_the_per_ip_budget(api_client: AsyncClient) -> None:
    for _ in range(ACCESS_REQUEST_PER_HOUR):
        allowed = await api_client.post("/api/v1/public/access-requests", json=_payload(_address()))
        assert allowed.status_code == 202, allowed.text

    limited = await api_client.post("/api/v1/public/access-requests", json=_payload(_address()))

    assert limited.status_code == 429
    assert limited.headers["retry-after"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"organization": "x"},
        {"name": ""},
        {"email": "not-an-address"},
        {"size": "one-million"},
        {"phone": "9" * 33},
    ],
)
async def test_a_malformed_form_is_rejected(
    api_client: AsyncClient, overrides: dict[str, object]
) -> None:
    body = _payload(_address())
    body.update(overrides)

    response = await api_client.post("/api/v1/public/access-requests", json=body)

    assert response.status_code == 422


async def test_signup_status_is_public_and_cacheable(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/public/signup-status")

    assert response.status_code == 200
    assert response.json() == {"open_signup": get_settings().allow_open_signup}
    assert response.headers["cache-control"] == "public, max-age=60"


async def test_the_admin_list_is_shut_to_anyone_who_is_not_a_platform_operator(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _email, _org_id = await _signup(api_client)
    _make_platform_admin(monkeypatch, "someone-else@blade.example")

    response = await api_client.get(
        "/api/v1/access-requests", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not a platform operator"


async def test_an_unverified_owner_with_an_operator_address_is_refused(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, operator_email, _org_id = await _signup(api_client)
    _make_platform_admin(monkeypatch, operator_email)

    response = await api_client.get(
        "/api/v1/access-requests", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "not a platform operator"


async def test_a_platform_operator_lists_and_triages_a_request(
    api_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = _address()
    created = await api_client.post("/api/v1/public/access-requests", json=_payload(email))
    assert created.status_code == 202

    token, operator_email, operator_org_id = await _signup(api_client)
    _make_platform_admin(monkeypatch, operator_email)
    await _verify_email(db_session, operator_email)
    headers = {"Authorization": f"Bearer {token}"}

    listed = await api_client.get("/api/v1/access-requests?limit=1", headers=headers)
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["items"][0]["email"] == email
    assert page["items"][0]["status"] == "new"
    assert page["next_cursor"]

    request_id = page["items"][0]["id"]
    patched = await api_client.patch(
        f"/api/v1/access-requests/{request_id}",
        headers=headers,
        json={"status": "contacted", "notes": "called on Tuesday"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "contacted"
    assert patched.json()["notes"] == "called on Tuesday"
    assert patched.json()["handled_by"] == operator_email
    assert patched.json()["handled_at"]

    audited = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == operator_org_id,
            AuditEvent.action == "access_request_updated",
            AuditEvent.metadata_json["access_request_id"].astext == request_id,
        )
    )
    assert audited is not None
    assert audited.actor == f"member:{operator_email}"
    assert audited.metadata_json["to_status"] == "contacted"
