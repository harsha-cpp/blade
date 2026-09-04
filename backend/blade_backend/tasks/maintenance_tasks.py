# pyright: reportUnknownMemberType=false, reportUntypedFunctionDecorator=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast

import structlog
from sqlalchemy import ColumnElement, CursorResult, create_engine, delete, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from blade_backend.db.base import Base
from blade_backend.db.models import (
    AccessRequest,
    DeviceToken,
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
)
from blade_backend.settings import get_settings
from blade_backend.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

PURGE_BATCH_SIZE = 1_000
PURGE_MAX_BATCHES = 100


@lru_cache(maxsize=1)
def _sync_engine() -> Engine:
    return create_engine(get_settings().database_sync_url, pool_pre_ping=True)


def _delete_in_batches(session: Session, model: type[Base], condition: ColumnElement[bool]) -> int:
    removed = 0
    identifier = model.__table__.c.id
    for _ in range(PURGE_MAX_BATCHES):
        doomed = select(identifier).where(condition).limit(PURGE_BATCH_SIZE).scalar_subquery()
        result = cast(
            CursorResult[Any], session.execute(delete(model).where(identifier.in_(doomed)))
        )
        session.commit()
        removed += result.rowcount
        if result.rowcount < PURGE_BATCH_SIZE:
            break
    return removed


def purge_stale_rows(now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=settings.token_retention_days)
    purged: dict[str, int] = {}

    with Session(_sync_engine()) as session:
        purged["refresh_tokens"] = _delete_in_batches(
            session,
            RefreshToken,
            or_(
                RefreshToken.expires_at < cutoff,
                RefreshToken.revoked_at < cutoff,
                RefreshToken.rotated_at < cutoff,
            ),
        )
        purged["password_reset_tokens"] = _delete_in_batches(
            session,
            PasswordResetToken,
            or_(
                PasswordResetToken.expires_at < cutoff,
                PasswordResetToken.used_at < cutoff,
            ),
        )
        purged["email_verification_tokens"] = _delete_in_batches(
            session,
            EmailVerificationToken,
            or_(
                EmailVerificationToken.expires_at < cutoff,
                EmailVerificationToken.used_at < cutoff,
            ),
        )
        purged["device_tokens"] = _delete_in_batches(
            session,
            DeviceToken,
            or_(DeviceToken.expires_at < cutoff, DeviceToken.revoked_at < cutoff),
        )
        retention_days = settings.access_request_retention_days
        purged["access_requests"] = (
            _delete_in_batches(
                session,
                AccessRequest,
                AccessRequest.created_at < moment - timedelta(days=retention_days),
            )
            if retention_days > 0
            else 0
        )

    logger.info("maintenance.purged_stale_rows", cutoff=cutoff.isoformat(), **purged)
    return purged


@celery_app.task(name="blade.maintenance.purge_expired_tokens")
def purge_expired_tokens() -> dict[str, int]:
    return purge_stale_rows()
