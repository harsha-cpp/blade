from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from blade_backend.api.v1.router import api_v1_router
from blade_backend.db.session import close_engine
from blade_backend.health import ReadinessProbe, build_readiness_probe
from blade_backend.observability.logging import configure_logging
from blade_backend.observability.security import (
    LANDING_CORS_PATHS,
    PathScopedCORSMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from blade_backend.scan.runner import shutdown_detection_pool
from blade_backend.settings import DEV_AUDIT_HMAC_KEY, Settings, get_settings

logger = structlog.get_logger(__name__)

ReadyzHandler = Callable[[], Awaitable[JSONResponse]]


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def warn_on_undefended_refresh_cookie(settings: Settings) -> None:
    if settings.refresh_cookie_samesite != "none" or settings.cors_origins:
        return
    logger.warning(
        "refresh cookie is samesite=none with no configured cors origins",
        remedy=(
            "set CORS_ORIGINS, or REFRESH_COOKIE_SAMESITE=lax: the cookie is sent "
            "cross-site and /auth/refresh has no allow-list to check the origin against"
        ),
    )


def warn_on_development_audit_key(settings: Settings) -> None:
    if settings.audit_hmac_key_value != DEV_AUDIT_HMAC_KEY:
        return
    logger.warning(
        "audit row signatures are keyed with the built-in development key",
        remedy=(
            "set AUDIT_HMAC_KEY: with the shared development key an attacker who can "
            "write audit rows can also forge their signatures"
        ),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    configure_logging()
    warn_on_undefended_refresh_cookie(settings)
    warn_on_development_audit_key(settings)
    await asyncio.to_thread(ensure_private_dir, settings.attachment_temp_dir)
    await asyncio.to_thread(ensure_private_dir, settings.quarantine_storage_dir)
    yield
    await asyncio.to_thread(shutdown_detection_pool)
    await close_engine()


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def build_readyz_handler(probe: ReadinessProbe) -> ReadyzHandler:
    async def readyz() -> JSONResponse:
        readiness = await probe()
        return JSONResponse(
            status_code=200 if readiness.ready else 503,
            content=readiness.as_dict(),
        )

    return readyz


def create_app(
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    probe = readiness_probe or build_readiness_probe(resolved_settings)

    app = FastAPI(
        title="Blade DLP Backend",
        version="0.1.0",
        docs_url=None if resolved_settings.hardened_config_required else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    if resolved_settings.landing_origins:
        app.add_middleware(
            PathScopedCORSMiddleware,
            paths=LANDING_CORS_PATHS,
            allow_origins=resolved_settings.landing_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["accept", "content-type"],
            max_age=600,
        )

    app.include_router(api_v1_router, prefix="/api/v1")
    app.add_api_route("/healthz", healthz, methods=["GET"], tags=["meta"])

    app.add_api_route(
        "/readyz",
        build_readyz_handler(probe),
        methods=["GET"],
        tags=["meta"],
    )

    return app


app = create_app()
