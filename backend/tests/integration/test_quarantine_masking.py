from __future__ import annotations

import uuid

import pytest
from blade_detection.models import EmailPayload, ScanResult
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import Organization, QuarantineItem
from blade_backend.scan import runner
from blade_backend.scan.api import UNSCANNED_SUBJECT_PLACEHOLDER
from blade_backend.scan.runner import DetectionTimeoutError
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

AADHAAR = "7534 7930 7460"
SUBJECT = f"Discharge summary {AADHAAR}"
SENDER = "doctor@sunrisehospital.in"
RECIPIENT = "patient@gmail.com"

QUARANTINE_UNREAD = {
    "version": "test-quarantine-unread",
    "rules": [
        {
            "id": "quarantine-unread",
            "description": "Quarantine anything that could not be read",
            "enabled": True,
            "order": 1,
            "conditions": {"min_unscanned_attachments": 1},
            "action": "quarantine",
            "user_message": "Held for review.",
        }
    ],
}

QUARANTINE_EVERYTHING = {
    "version": "test-quarantine-all",
    "rules": [
        {
            "id": "quarantine-aadhaar",
            "description": "Quarantine anything carrying an Aadhaar number",
            "enabled": True,
            "order": 1,
            "conditions": {"entity_types_any": ["IN_AADHAAR"]},
            "action": "quarantine",
            "user_message": "Held for review.",
        }
    ],
}


async def _signup(client: AsyncClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Masking Hospital {suffix}",
            "email": f"owner-{suffix}@sunrisehospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["access_token"], payload["organization"]["org_code"]


async def test_a_quarantined_subject_is_stored_masked(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_code = await _signup(api_client)
    policy = await api_client.put(
        "/api/v1/policy",
        headers={"Authorization": f"Bearer {token}"},
        json=QUARANTINE_EVERYTHING,
    )
    assert policy.status_code == 200, policy.text

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": SUBJECT,
            "body": "Please see the attached discharge summary.",
            "recipients": [RECIPIENT],
            "user_email": SENDER,
        },
    )
    assert response.status_code == 200, response.text
    verdict = response.json()
    assert verdict["action"] == "quarantine", verdict

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    item = await db_session.scalar(select(QuarantineItem).where(QuarantineItem.org_id == org_id))
    assert item is not None
    assert AADHAAR not in item.subject
    assert "7534" not in item.subject
    assert item.subject.startswith("Discharge summary ")
    assert "*" in item.subject
    assert item.sender == SENDER
    assert item.recipients == [RECIPIENT]


async def test_a_subject_with_nothing_sensitive_is_stored_as_written(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_code = await _signup(api_client)
    policy = await api_client.put(
        "/api/v1/policy",
        headers={"Authorization": f"Bearer {token}"},
        json=QUARANTINE_EVERYTHING,
    )
    assert policy.status_code == 200, policy.text

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Ward rota for next week",
            "body": f"Aadhaar {AADHAAR} is in the body only.",
            "recipients": [RECIPIENT],
            "user_email": SENDER,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "quarantine"

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    item = await db_session.scalar(select(QuarantineItem).where(QuarantineItem.org_id == org_id))
    assert item is not None
    assert item.subject == "Ward rota for next week"


async def test_a_subject_is_never_stored_when_detection_did_not_run(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, org_code = await _signup(api_client)
    policy = await api_client.put(
        "/api/v1/policy",
        headers={"Authorization": f"Bearer {token}"},
        json=QUARANTINE_UNREAD,
    )
    assert policy.status_code == 200, policy.text

    async def _times_out(_payload: EmailPayload) -> ScanResult:
        raise DetectionTimeoutError("detection exceeded its deadline")

    monkeypatch.setattr(runner, "run_detection", _times_out)

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": SUBJECT,
            "body": "Please see the attached discharge summary.",
            "recipients": [RECIPIENT],
            "user_email": SENDER,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "quarantine"

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    item = await db_session.scalar(select(QuarantineItem).where(QuarantineItem.org_id == org_id))
    assert item is not None
    assert item.subject == UNSCANNED_SUBJECT_PLACEHOLDER
    assert AADHAAR not in item.subject
    assert "Discharge summary" not in item.subject
