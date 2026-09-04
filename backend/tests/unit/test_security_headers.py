from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from blade_backend.main import create_app
from blade_backend.observability.security import SecurityHeadersMiddleware, carries_credentials
from blade_backend.settings import Settings

DASHBOARD_ORIGIN = "https://dashboard.example"


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/signup",
        "/api/v1/auth/switch-org",
        "/api/v1/auth/mfa/verify",
        "/api/v1/members/accept-invite",
        "/api/v1/devices/enroll",
    ],
)
def test_every_token_bearing_route_is_marked_uncacheable(path: str) -> None:
    assert carries_credentials(path) is True


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/v1/events", "/api/v1/public/signup-status", "/healthz"])
def test_ordinary_routes_keep_their_own_caching(path: str) -> None:
    assert carries_credentials(path) is False


async def _token_response(_request: object) -> JSONResponse:
    return JSONResponse({"access_token": "not-a-real-token"})


@pytest.mark.unit
async def test_a_token_bearing_response_is_marked_no_store() -> None:
    app = Starlette(
        routes=[
            Route("/api/v1/auth/login", _token_response, methods=["POST"]),
            Route("/api/v1/events", _token_response, methods=["POST"]),
        ]
    )
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login")
        events = await client.post("/api/v1/events")

    assert login.headers["cache-control"] == "no-store"
    assert login.headers["x-content-type-options"] == "nosniff"
    assert "cache-control" not in events.headers


@pytest.mark.unit
@pytest.mark.parametrize(
    ("app_env", "docs_visible"),
    [("local", True), ("test", True), ("staging", False), ("production", False)],
)
def test_interactive_docs_are_hidden_outside_development(app_env: str, docs_visible: bool) -> None:
    settings = Settings(
        app_env=app_env,
        refresh_cookie_secure=True,
        jwt_secret="a-real-secret-that-is-long-enough",  # type: ignore[arg-type]
        storage_backend="s3",
        mailer_backend="smtp",
        audit_hmac_key="a-real-audit-key-that-is-long-enough",  # type: ignore[arg-type]
    )

    app = create_app(settings)

    assert (app.docs_url == "/docs") is docs_visible


LANDING_ORIGIN = "https://bladedlp.example"


def _landing_app() -> Starlette:
    return create_app(Settings(landing_origins=[LANDING_ORIGIN], cors_origins=[DASHBOARD_ORIGIN]))


@pytest.mark.unit
async def test_the_landing_origin_may_post_an_access_request() -> None:
    transport = ASGITransport(app=_landing_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        preflight = await client.options(
            "/api/v1/public/access-requests",
            headers={
                "origin": LANDING_ORIGIN,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == LANDING_ORIGIN


@pytest.mark.unit
async def test_the_landing_origin_reads_signup_status() -> None:
    transport = ASGITransport(app=_landing_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/public/signup-status", headers={"origin": LANDING_ORIGIN}
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LANDING_ORIGIN


@pytest.mark.unit
async def test_the_landing_origin_is_not_allowed_anywhere_else() -> None:
    transport = ASGITransport(app=_landing_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        elsewhere = await client.get("/healthz", headers={"origin": LANDING_ORIGIN})
        dashboard = await client.get("/healthz", headers={"origin": DASHBOARD_ORIGIN})

    assert "access-control-allow-origin" not in elsewhere.headers
    assert dashboard.headers["access-control-allow-origin"] == DASHBOARD_ORIGIN
