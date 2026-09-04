from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from blade_backend.email import set_mailer
from blade_backend.public.api import (
    AccessRequestIn,
    build_access_request,
    notification_fields,
    notify_access_request,
)

HONEYPOT = "https://spam.example/bot-filled-this"


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


class _FailingMailer:
    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, *, to: str, subject: str, body: str) -> None:
        del to, subject, body
        self.attempts += 1
        raise RuntimeError("smtp is down")


def _fields() -> dict[str, str | None]:
    payload = AccessRequestIn(
        organization="City General Hospital",
        name="Asha Rao",
        email="asha@citygeneral.example",
        phone="+91 90000 00000",
        role="Chief information security officer",
        size="500-1000",
        message="We want a pilot on the oncology team.",
        website=HONEYPOT,
    )
    row = build_access_request(
        payload,
        client_ip="203.0.113.9",
        user_agent="Mozilla/5.0",
        created_at=datetime(2026, 9, 4, 10, 30, tzinfo=UTC),
    )
    return notification_fields(row)


@pytest.fixture(autouse=True, name="_restore_default_mailer")
def restore_default_mailer() -> Iterator[None]:
    yield
    set_mailer(None)


@pytest.mark.unit
async def test_notification_carries_the_form_fields_and_never_the_honeypot() -> None:
    mailer = _RecordingMailer()
    set_mailer(mailer)

    await notify_access_request("ops@blade.example", _fields())

    to, subject, body = mailer.sent[0]
    assert to == "ops@blade.example"
    assert "City General Hospital" in subject
    for expected in (
        "City General Hospital",
        "Asha Rao",
        "asha@citygeneral.example",
        "+91 90000 00000",
        "Chief information security officer",
        "500-1000",
        "We want a pilot on the oncology team.",
        "2026-09-04T10:30:00+00:00",
    ):
        assert expected in body
    assert HONEYPOT not in body
    assert "website" not in body.lower()


@pytest.mark.unit
async def test_a_mailer_failure_is_swallowed_so_the_stored_row_still_wins() -> None:
    mailer = _FailingMailer()
    set_mailer(mailer)

    await notify_access_request("ops@blade.example", _fields())

    assert mailer.attempts == 1
