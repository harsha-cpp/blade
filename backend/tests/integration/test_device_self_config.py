from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from blade_backend.scan.limits import scan_rate_limiter
from blade_backend.settings import get_settings
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]


async def _signup(client: AsyncClient) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:10]
    email = f"owner-{suffix}@meridianclinic.in"
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Meridian Clinic {suffix}",
            "email": email,
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["organization"]["org_code"], email


async def _enroll_device(client: AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/v1/devices/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Ward round tablet"},
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()["device_token"]


async def _add_domain(
    client: AsyncClient,
    token: str,
    *,
    domain: str,
    direction: str,
    classification: str,
) -> None:
    response = await client.post(
        "/api/v1/domains",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": domain, "direction": direction, "classification": classification},
    )
    assert response.status_code in {200, 201}, response.text


async def test_device_self_config_matches_the_public_config(api_client: AsyncClient) -> None:
    token, org_code, owner_email = await _signup(api_client)
    await _add_domain(
        api_client,
        token,
        domain="meridianclinic.in",
        direction="both",
        classification="internal",
    )
    await _add_domain(
        api_client,
        token,
        domain="labpartner.in",
        direction="recipient",
        classification="partner",
    )
    await _add_domain(
        api_client,
        token,
        domain="leaky-inbox.example",
        direction="both",
        classification="blocked",
    )
    device_token = await _enroll_device(api_client, token)

    public = await api_client.get(f"/api/v1/public/orgs/{org_code}/config")
    assert public.status_code == 200, public.text
    public_body = public.json()

    response = await api_client.get(
        "/api/v1/devices/me",
        headers={"X-Blade-Device-Token": device_token},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["config"]["domains"] == public_body["domains"]
    assert body["config"]["blocked_domains"] == public_body["blocked_domains"]
    assert body["config"]["fail_open"] == public_body["fail_open"]
    assert body["organization"] == {"name": public_body["organization"]["name"]}
    assert body["device"]["label"] == "Ward round tablet"
    assert body["device"]["member_email"] == owner_email
    assert body["device"]["expires_at"]
    assert uuid.UUID(body["device"]["id"])
    assert org_code not in response.text
    assert "org_code" not in response.text


async def test_device_self_config_requires_a_device_token(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/devices/me")

    assert response.status_code == 401


async def test_revoked_device_cannot_read_its_config(api_client: AsyncClient) -> None:
    token, _org_code, _email = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)

    listing = await api_client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200, listing.text
    device_id = listing.json()[0]["id"]
    revoked = await api_client.post(
        f"/api/v1/devices/{device_id}/revoke",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoked.status_code in {200, 204}, revoked.text

    response = await api_client.get(
        "/api/v1/devices/me",
        headers={"X-Blade-Device-Token": device_token},
    )

    assert response.status_code == 401


async def test_device_self_config_is_rate_limited_per_device(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _org_code, _email = await _signup(api_client)
    device_token = await _enroll_device(api_client, token)
    settings = get_settings()
    monkeypatch.setattr(settings, "scan_rate_limit_per_device_per_minute", 1)
    scan_rate_limiter.reset()

    first = await api_client.get(
        "/api/v1/devices/me",
        headers={"X-Blade-Device-Token": device_token},
    )
    second = await api_client.get(
        "/api/v1/devices/me",
        headers={"X-Blade-Device-Token": device_token},
    )
    scan_rate_limiter.reset()

    assert first.status_code == 200, first.text
    assert second.status_code == 429
