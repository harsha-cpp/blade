from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from blade_backend.main import create_app, warn_on_undefended_refresh_cookie
from blade_backend.settings import Settings, get_settings

ALLOWED_ORIGIN = "https://dashboard.blade.test"
HOSTILE_ORIGIN = "https://evil.example"


@pytest.fixture(name="_allowed_origins")
def allowed_origins(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CORS_ORIGINS", ALLOWED_ORIGIN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(name="cookie_client")
async def cookie_client(_allowed_origins: None) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/v1/auth/refresh", "/api/v1/auth/logout"])
async def test_a_foreign_origin_cannot_use_the_refresh_cookie(
    cookie_client: AsyncClient, path: str
) -> None:
    response = await cookie_client.post(
        path,
        headers={"origin": HOSTILE_ORIGIN},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "cross-site cookie request refused"


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/v1/auth/refresh", "/api/v1/auth/logout"])
async def test_a_foreign_referer_cannot_use_the_refresh_cookie(
    cookie_client: AsyncClient, path: str
) -> None:
    response = await cookie_client.post(
        path,
        headers={"referer": f"{HOSTILE_ORIGIN}/attack.html"},
    )

    assert response.status_code == 403


@pytest.mark.unit
async def test_the_dashboard_origin_is_let_through_to_the_cookie_check(
    cookie_client: AsyncClient,
) -> None:
    response = await cookie_client.post("/api/v1/auth/refresh", headers={"origin": ALLOWED_ORIGIN})

    assert response.status_code == 401
    assert response.json()["detail"] == "missing refresh token"


@pytest.mark.unit
async def test_the_dashboard_referer_is_let_through_to_the_cookie_check(
    cookie_client: AsyncClient,
) -> None:
    response = await cookie_client.post(
        "/api/v1/auth/refresh", headers={"referer": f"{ALLOWED_ORIGIN}/settings"}
    )

    assert response.status_code == 401


@pytest.mark.unit
async def test_a_non_browser_client_naming_no_origin_is_let_through(
    cookie_client: AsyncClient,
) -> None:
    response = await cookie_client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing refresh token"


@pytest.mark.unit
async def test_logout_from_the_dashboard_origin_clears_the_cookie(
    cookie_client: AsyncClient,
) -> None:
    response = await cookie_client.post("/api/v1/auth/logout", headers={"origin": ALLOWED_ORIGIN})

    assert response.status_code == 204


@pytest.mark.unit
async def test_preflight_allows_put_on_the_policy_endpoint() -> None:
    app = create_app(settings=Settings(cors_origins=[ALLOWED_ORIGIN]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "OPTIONS",
            "/api/v1/policy",
            headers={
                "origin": ALLOWED_ORIGIN,
                "access-control-request-method": "PUT",
            },
        )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


@pytest.mark.unit
def test_samesite_none_without_origins_is_warned_about(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(refresh_cookie_samesite="none", cors_origins=[])

    warn_on_undefended_refresh_cookie(settings)

    assert "samesite=none" in capsys.readouterr().out


@pytest.mark.unit
def test_configured_origins_silence_the_samesite_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(refresh_cookie_samesite="none", cors_origins=[ALLOWED_ORIGIN])

    warn_on_undefended_refresh_cookie(settings)

    assert capsys.readouterr().out == ""
