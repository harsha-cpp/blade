from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

AADHAAR = "7534 7930 7460"

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
            "org_name": f"Polling Hospital {suffix}",
            "email": f"owner-{suffix}@pollinghospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["access_token"], payload["organization"]["org_code"]


async def _enroll_device(client: AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/v1/devices/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Reception desktop"},
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()["device_token"]


async def _quarantine_a_message(client: AsyncClient, token: str, device_token: str) -> str:
    policy = await client.put(
        "/api/v1/policy",
        headers={"Authorization": f"Bearer {token}"},
        json=QUARANTINE_EVERYTHING,
    )
    assert policy.status_code == 200, policy.text

    response = await client.post(
        "/api/v1/scan/email",
        headers={"X-Blade-Device-Token": device_token},
        json={
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Discharge summary",
            "body": f"Aadhaar {AADHAAR} for the discharge summary.",
            "recipients": ["patient@gmail.com"],
        },
    )
    assert response.status_code == 200, response.text
    verdict = response.json()
    assert verdict["action"] == "quarantine", verdict
    quarantine_id = verdict["quarantine_id"]
    assert quarantine_id
    return str(quarantine_id)


async def test_a_device_polls_quarantine_status_without_an_org_code(
    api_client: AsyncClient,
) -> None:
    token, _org_code = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)
    quarantine_id = await _quarantine_a_message(api_client, token, device_token)

    response = await api_client.get(
        f"/api/v1/quarantine/{quarantine_id}/status",
        headers={"X-Blade-Device-Token": device_token},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quarantine_id"] == quarantine_id
    assert body["status"] == "pending"
    assert body["decided_at"] is None


async def test_an_org_code_still_polls_quarantine_status(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)
    quarantine_id = await _quarantine_a_message(api_client, token, device_token)

    response = await api_client.get(
        f"/api/v1/quarantine/{quarantine_id}/status",
        params={"org_code": org_code},
    )

    assert response.status_code == 200, response.text
    assert response.json()["quarantine_id"] == quarantine_id


async def test_a_device_from_another_org_cannot_see_the_item(api_client: AsyncClient) -> None:
    token, _org_code = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)
    quarantine_id = await _quarantine_a_message(api_client, token, device_token)

    other_token, _other_code = await _signup(api_client)
    other_device_token = await _enroll_device(api_client, other_token)

    response = await api_client.get(
        f"/api/v1/quarantine/{quarantine_id}/status",
        headers={"X-Blade-Device-Token": other_device_token},
    )

    assert response.status_code == 404


async def test_polling_without_any_credential_is_rejected(api_client: AsyncClient) -> None:
    token, _org_code = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)
    quarantine_id = await _quarantine_a_message(api_client, token, device_token)

    response = await api_client.get(f"/api/v1/quarantine/{quarantine_id}/status")

    assert response.status_code == 401
