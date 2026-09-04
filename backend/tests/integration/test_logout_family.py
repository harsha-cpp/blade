from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from blade_backend.auth.jwt import REFRESH_SEPARATOR
from blade_backend.settings import get_settings
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PASSWORD = "correct-horse-battery-staple-9"


async def _signup(client: AsyncClient) -> str:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Logout Hospital {suffix}",
            "email": f"owner-{suffix}@logouthospital.in",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return client.cookies[get_settings().refresh_cookie_name]


def _forged(cookie: str) -> str:
    token_id, _separator, secret = cookie.partition(REFRESH_SEPARATOR)
    return f"{token_id}{REFRESH_SEPARATOR}{'z' * len(secret)}"


async def _post(client: AsyncClient, path: str, cookie: str) -> int:
    client.cookies.clear()
    name = get_settings().refresh_cookie_name
    response = await client.post(path, headers={"cookie": f"{name}={cookie}"})
    return response.status_code


async def test_a_guessed_token_id_cannot_sign_a_family_out(api_client: AsyncClient) -> None:
    cookie = await _signup(api_client)

    assert await _post(api_client, "/api/v1/auth/logout", _forged(cookie)) == 204
    assert await _post(api_client, "/api/v1/auth/refresh", cookie) == 200


async def test_the_real_cookie_still_revokes_the_family(api_client: AsyncClient) -> None:
    cookie = await _signup(api_client)

    assert await _post(api_client, "/api/v1/auth/logout", cookie) == 204
    assert await _post(api_client, "/api/v1/auth/refresh", cookie) == 401
