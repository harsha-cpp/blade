from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

BODY_WITH_PHI = "Patient Lakshmi Devi, UHID 0024518, Aadhaar 7534 7930 7460."


async def _signup(client: AsyncClient) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Paged Hospital {suffix}",
            "email": f"owner-{suffix}@pagedhospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return str(body["access_token"]), str(body["organization"]["org_code"])


async def _quarantine_three(client: AsyncClient, headers: dict[str, str], org_code: str) -> None:
    await client.post(
        "/api/v1/domains",
        headers=headers,
        json={"domain": "pagedhospital.in", "classification": "internal", "direction": "both"},
    )
    for _ in range(3):
        scanned = await client.post(
            "/api/v1/scan/email",
            json={
                "org_code": org_code,
                "client_scan_id": uuid.uuid4().hex,
                "subject": "Discharge summary",
                "body": BODY_WITH_PHI,
                "recipients": ["patient@gmail.com"],
                "user_email": "doctor@pagedhospital.in",
            },
        )
        assert scanned.status_code == 200, scanned.text


async def test_the_cursor_walks_every_page_of_the_queue(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    headers = {"Authorization": f"Bearer {token}"}
    await _quarantine_three(api_client, headers, org_code)

    first = await api_client.get("/api/v1/quarantine?status=all&limit=2", headers=headers)
    assert first.status_code == 200, first.text
    first_page: dict[str, Any] = first.json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"]

    second = await api_client.get(
        f"/api/v1/quarantine?status=all&limit=2&cursor={first_page['next_cursor']}",
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_page: dict[str, Any] = second.json()
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None

    seen = [item["id"] for item in first_page["items"]] + [
        item["id"] for item in second_page["items"]
    ]
    assert len(set(seen)) == 3

    timestamps = [item["created_at"] for item in first_page["items"] + second_page["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_a_bad_cursor_is_a_client_error(api_client: AsyncClient) -> None:
    token, _org_code = await _signup(api_client)

    response = await api_client.get(
        "/api/v1/quarantine?cursor=not-base64!!",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid cursor"


async def test_only_one_of_two_simultaneous_reviewers_decides(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    headers = {"Authorization": f"Bearer {token}"}
    await _quarantine_three(api_client, headers, org_code)

    listed = await api_client.get("/api/v1/quarantine?status=pending&limit=1", headers=headers)
    assert listed.status_code == 200, listed.text
    item_id = listed.json()["items"][0]["id"]

    approve, reject = await asyncio.gather(
        api_client.post(
            f"/api/v1/quarantine/{item_id}/approve", headers=headers, json={"note": "release"}
        ),
        api_client.post(
            f"/api/v1/quarantine/{item_id}/reject", headers=headers, json={"note": "hold"}
        ),
    )

    outcomes = sorted([approve.status_code, reject.status_code])
    assert outcomes == [200, 409], (approve.text, reject.text)

    settled = await api_client.get(f"/api/v1/quarantine/{item_id}", headers=headers)
    assert settled.json()["status"] in {"approved", "rejected"}
    assert settled.json()["decided_at"]
