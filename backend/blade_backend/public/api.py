from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.auth.rate_limit import access_request_limiter
from blade_backend.db.models import (
    AccessRequest,
    AccessRequestSize,
    Organization,
)
from blade_backend.deps import DbSession
from blade_backend.email import get_mailer, send_quietly
from blade_backend.email.templates import access_request_email
from blade_backend.observability.security import resolve_client_ip
from blade_backend.public.config import PublicDomain, build_org_config
from blade_backend.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter()

SIGNUP_STATUS_MAX_AGE_SECONDS = 60
USER_AGENT_MAX_LENGTH = 300
DEDUPE_WINDOW = timedelta(hours=24)
_UNAVAILABLE_DETAIL = "access requests cannot be accepted right now"


class PublicOrg(BaseModel):
    name: str
    org_code: str


class PublicConfig(BaseModel):
    organization: PublicOrg
    fail_open: bool = False
    domains: list[PublicDomain]
    blocked_domains: list[PublicDomain]


@router.get("/orgs/{org_code}/config", response_model=PublicConfig)
async def get_public_config(org_code: str, session: DbSession) -> PublicConfig:
    normalized = org_code.strip().upper()
    org = await session.scalar(select(Organization).where(Organization.org_code == normalized))
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown org code")

    config = await build_org_config(session, org)
    return PublicConfig(
        organization=PublicOrg(name=org.name, org_code=org.org_code),
        fail_open=config.fail_open,
        domains=config.domains,
        blocked_domains=config.blocked_domains,
    )


class AccessRequestIn(BaseModel):
    organization: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    role: str | None = Field(default=None, max_length=80)
    size: AccessRequestSize | None = Field(default=None)
    message: str | None = Field(default=None, max_length=2000)
    website: str | None = Field(default=None, max_length=2000)

    @field_validator("organization", "name", mode="before")
    @classmethod
    def strip_required(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone", "role", "message", "website", mode="before")
    @classmethod
    def blank_as_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class AccessRequestAccepted(BaseModel):
    status: Literal["received"] = "received"


class SignupStatus(BaseModel):
    open_signup: bool


def notification_fields(row: AccessRequest) -> dict[str, str | None]:
    return {
        "organization": row.organization,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "role": row.role,
        "size": row.size,
        "message": row.message,
        "submitted_at": row.created_at.isoformat(),
    }


async def notify_access_request(recipient: str, fields: dict[str, str | None]) -> None:
    subject, body = access_request_email(
        organization=str(fields["organization"]),
        name=str(fields["name"]),
        email=str(fields["email"]),
        phone=fields["phone"],
        role=fields["role"],
        size=fields["size"],
        message=fields["message"],
        submitted_at=str(fields["submitted_at"]),
    )
    delivered = await send_quietly(get_mailer(), to=recipient, subject=subject, body=body)
    if not delivered:
        logger.warning("access_request.notification_failed", to=recipient)


async def _recent_duplicate(
    session: AsyncSession, email: str, now: datetime
) -> AccessRequest | None:
    return await session.scalar(
        select(AccessRequest)
        .where(AccessRequest.email == email, AccessRequest.created_at >= now - DEDUPE_WINDOW)
        .order_by(AccessRequest.created_at.desc())
        .limit(1)
    )


def build_access_request(
    payload: AccessRequestIn,
    *,
    client_ip: str,
    user_agent: str | None,
    created_at: datetime,
) -> AccessRequest:
    return AccessRequest(
        created_at=created_at,
        organization=payload.organization,
        name=payload.name,
        email=str(payload.email).strip().lower(),
        phone=payload.phone,
        role=payload.role,
        size=payload.size,
        message=payload.message,
        client_ip=client_ip,
        user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
    )


async def _persist_access_request(
    session: AsyncSession,
    payload: AccessRequestIn,
    request: Request,
) -> AccessRequest | None:
    now = datetime.now(UTC)
    row = build_access_request(
        payload,
        client_ip=resolve_client_ip(request, get_settings().trusted_proxy_count),
        user_agent=request.headers.get("user-agent"),
        created_at=now,
    )
    existing = await _recent_duplicate(session, row.email, now)
    if existing is not None:
        submitted = payload.message or ""
        if len(submitted) > len(existing.message or ""):
            existing.message = submitted
        await session.commit()
        return None

    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post(
    "/access-requests",
    response_model=AccessRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_access_request(
    payload: AccessRequestIn,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
) -> AccessRequestAccepted:
    """Take an access request from the marketing site.

    Anonymous and unauthenticated, so it is rate limited per client IP on top
    of the global middleware budget, and a filled `website` field is treated as
    a bot: the same 202 comes back and nothing at all is stored or mailed.

    The same address inside 24 hours updates the existing row rather than
    adding another, so a double-submitted form is one lead. The stored row is
    the record of truth; the operator notification is best effort and a mail
    failure never turns into an error for the visitor.
    """
    limit = await access_request_limiter.check(request)
    if not limit.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many access requests",
            headers={"retry-after": str(limit.retry_after_seconds)},
        )

    if payload.website:
        return AccessRequestAccepted()

    try:
        stored = await _persist_access_request(session, payload, request)
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.warning("access_request.store_failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=_UNAVAILABLE_DETAIL
        ) from exc

    recipient = get_settings().access_request_notify_email
    if stored is not None and recipient:
        background_tasks.add_task(notify_access_request, recipient, notification_fields(stored))
    return AccessRequestAccepted()


@router.get("/signup-status", response_model=SignupStatus)
async def get_signup_status(response: Response) -> SignupStatus:
    """Tell the marketing site whether self-serve signup is open.

    Cached for a minute: the landing page asks on every visit and the answer
    only changes when an operator edits ALLOW_OPEN_SIGNUP and restarts.
    """
    response.headers["cache-control"] = f"public, max-age={SIGNUP_STATUS_MAX_AGE_SECONDS}"
    return SignupStatus(open_signup=get_settings().allow_open_signup)
