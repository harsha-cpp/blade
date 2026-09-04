from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.audit.service import write_audit_event
from blade_backend.auth.jwt import (
    TokenError,
    issue_access_token,
    issue_refresh_token,
    parse_refresh_token,
    verify_refresh_secret,
)
from blade_backend.auth.mfa import CHALLENGE_TTL_SECONDS, issue_challenge_token
from blade_backend.auth.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from blade_backend.auth.rate_limit import login_rate_limiter
from blade_backend.auth.session import (
    AuthResponse,
    MemberView,
    MfaChallengeResponse,
    OrganizationView,
    clear_refresh_cookie,
    issue_session,
    read_refresh_cookie,
    request_ip,
    request_user_agent,
    serialize_member,
    serialize_org,
    set_refresh_cookie,
)
from blade_backend.auth.tokens import (
    EMAIL_VERIFY_PREFIX,
    PASSWORD_RESET_PREFIX,
    TokenFormatError,
    issue_token,
    parse_token,
    verify_token_secret,
)
from blade_backend.db.models import (
    EmailVerificationToken,
    MemberMfa,
    Organization,
    OrgMember,
    PasswordResetToken,
    RefreshToken,
)
from blade_backend.deps import CurrentMember, DbSession
from blade_backend.email import get_mailer, send_quietly
from blade_backend.email.templates import email_verification_email, password_reset_email
from blade_backend.settings import get_settings

router = APIRouter()

_ORG_CODE_BYTES = 18
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SWITCH_DENIED_DETAIL = "no verified active membership in that org"
_CROSS_SITE_DETAIL = "cross-site cookie request refused"
_RESET_TOKEN_DETAIL = "reset link is invalid or has expired"
_VERIFY_TOKEN_DETAIL = "verification link is invalid or has expired"

__all__ = ["AuthResponse", "MemberView", "OrganizationView", "router"]


def _slugify(name: str) -> str:
    base = _SLUG_RE.sub("-", name.lower()).strip("-")
    return base or "org"


def _generate_org_code() -> str:
    suffix = secrets.token_urlsafe(_ORG_CODE_BYTES).replace("-", "").replace("_", "")
    return "BLD-" + suffix.upper()


async def _unique_slug(session: AsyncSession, base: str) -> str:
    for suffix in [""] + [f"-{secrets.token_hex(2)}" for _ in range(5)]:
        candidate = f"{base}{suffix}"
        existing = await session.scalar(
            select(Organization.id).where(Organization.slug == candidate)
        )
        if existing is None:
            return candidate
    return f"{base}-{secrets.token_hex(4)}"


async def _unique_org_code(session: AsyncSession) -> str:
    for _ in range(10):
        code = _generate_org_code()
        existing = await session.scalar(
            select(Organization.id).where(Organization.org_code == code)
        )
        if existing is None:
            return code
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="unable to allocate org code")


async def _mfa_enabled(session: AsyncSession, member_id: UUID) -> bool:
    confirmed = await session.scalar(
        select(MemberMfa.confirmed_at).where(MemberMfa.member_id == member_id)
    )
    return confirmed is not None


def _enforce_browser_origin(request: Request) -> None:
    allowed = set(get_settings().cors_origins)
    origin = request.headers.get("origin")
    if origin is not None:
        if origin not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_CROSS_SITE_DETAIL)
        return
    referer = request.headers.get("referer")
    if referer is None:
        return
    parsed = urlsplit(referer)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_CROSS_SITE_DETAIL)
    if f"{parsed.scheme}://{parsed.netloc}" not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_CROSS_SITE_DETAIL)


def _enforce_password_policy(password: str, email: str) -> None:
    try:
        validate_password(password, email=email)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


class SignupRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    org_slug: str | None = Field(default=None, min_length=2, max_length=120)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=12, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class SwitchOrgRequest(BaseModel):
    org_id: UUID


class SessionOut(BaseModel):
    id: str
    created_at: str
    last_used_at: str | None
    user_agent: str | None
    ip_address: str | None
    current: bool


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    response: Response,
    request: Request,
    session: DbSession,
) -> AuthResponse:
    settings = get_settings()
    if not settings.allow_open_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="open signup is disabled")

    email = str(payload.email).lower().strip()
    _enforce_password_policy(payload.password, email)
    slug = await _unique_slug(session, _slugify(payload.org_name))
    org_code = await _unique_org_code(session)
    password_hash = await asyncio.to_thread(hash_password, payload.password)

    try:
        org = Organization(name=payload.org_name.strip(), slug=slug, org_code=org_code)
        session.add(org)
        await session.flush()

        member = OrgMember(
            org_id=org.id,
            email=email,
            name=payload.name,
            password_hash=password_hash,
            role="owner",
            status="active",
        )
        session.add(member)
        await session.flush()
        await _send_verification_email(session, member)
        return await issue_session(session, response, member, org, request=request)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="organization or member already exists"
        ) from exc


@router.post(
    "/login",
    response_model=AuthResponse | MfaChallengeResponse,
    response_model_exclude_none=True,
)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: DbSession,
) -> AuthResponse | MfaChallengeResponse:
    email = str(payload.email).lower().strip()
    limit = await login_rate_limiter.check(request, email)
    if not limit.allowed:
        if limit.audit_due:
            await _audit_login_lockout(
                session,
                email=email,
                org_slug=payload.org_slug,
                retry_after_seconds=limit.retry_after_seconds,
            )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": str(limit.retry_after_seconds)},
        )
    members = await _login_members(session, email, payload.org_slug)
    if not members:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    verified_members: list[OrgMember] = []
    for candidate in members:
        if candidate.password_hash is None:
            continue
        password_ok = await asyncio.to_thread(
            verify_password,
            payload.password,
            candidate.password_hash,
        )
        if password_ok:
            verified_members.append(candidate)

    if not verified_members:
        await _audit_login(session, members=members, email=email, action="login_failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if payload.org_slug is None and len(verified_members) > 1:
        choices = await _org_choices(session, verified_members)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "org_selection_required",
                "organizations": [choice.model_dump() for choice in choices],
            },
        )

    member = verified_members[0]
    org = await session.get(Organization, member.org_id)
    if org is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="organization missing")

    if await _mfa_enabled(session, member.id):
        await _audit_login(
            session,
            members=[member],
            email=email,
            action="login_mfa_challenged",
        )
        return MfaChallengeResponse(
            challenge_token=issue_challenge_token(member.id, org.id),
            expires_in=CHALLENGE_TTL_SECONDS,
        )
    await write_audit_event(
        session,
        org_id=org.id,
        actor=f"member:{member.email}",
        category="auth",
        action="login_succeeded",
        metadata={"member_id": str(member.id), "factor": "password"},
    )
    return await issue_session(session, response, member, org, request=request)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(response: Response, request: Request, session: DbSession) -> AuthResponse:
    """Rotate the refresh token and mint a new access token.

    The old token stays usable for ``refresh_rotation_grace_seconds`` so two
    dashboard tabs refreshing at once do not sign each other out. Presented
    after that window it can only have been copied, so the whole family - every
    descendant of that login - is revoked. Rotation is claimed with a
    conditional `UPDATE ... WHERE rotated_at IS NULL`, so two tabs racing
    cannot both mint a child and orphan one of them.

    The cookie is the only credential here, so a browser that names an origin
    or a referring page must name one of `CORS_ORIGINS`. Clients that send
    neither header are not browsers and cannot be made to carry the cookie
    from somebody else's page, so they are allowed through.
    """
    _enforce_browser_origin(request)
    settings = get_settings()
    refresh_cookie = read_refresh_cookie(request, settings)
    if not refresh_cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing refresh token")
    try:
        token_id, secret = parse_refresh_token(refresh_cookie)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token") from exc

    record = await session.get(RefreshToken, token_id)
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token")
    secret_ok = await asyncio.to_thread(verify_refresh_secret, secret, record.token_hash)
    if not secret_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token")

    now = datetime.now(UTC)
    if record.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token expired")
    if record.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token revoked")

    member = await session.get(OrgMember, record.member_id)
    if member is None or member.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="member inactive")
    org = await session.get(Organization, member.org_id)
    if org is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="organization missing")

    replayed = record.rotated_at is not None and now - record.rotated_at > timedelta(
        seconds=settings.refresh_rotation_grace_seconds
    )
    if replayed:
        await _revoke_family(session, record, now)
        await write_audit_event(
            session,
            org_id=org.id,
            actor=f"member:{member.email}",
            category="auth",
            action="refresh_token_reuse_detected",
            metadata={"family_id": str(record.family_id), "token_id": str(record.id)},
        )
        await session.commit()
        clear_refresh_cookie(response, settings)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token reuse detected")

    mfa_enabled = await _mfa_enabled(session, member.id)
    access_token = issue_access_token(str(member.id), str(org.id), member.role)
    rotation_won = record.rotated_at is None and await _claim_rotation(session, record, now)
    record.last_used_at = now

    if rotation_won:
        issued = await asyncio.to_thread(
            issue_refresh_token, ttl_days=settings.jwt_refresh_ttl_days
        )
        record.rotated_at = now
        session.add(
            RefreshToken(
                id=issued.id,
                member_id=member.id,
                family_id=record.family_id,
                token_hash=issued.token_hash,
                expires_at=issued.expires_at,
                last_used_at=now,
                user_agent=request_user_agent(request),
                ip_address=request_ip(request),
            )
        )
        await session.commit()
        set_refresh_cookie(response, issued.raw_token, settings, issued.expires_at)
    else:
        await session.commit()

    return AuthResponse(
        access_token=access_token,
        expires_in=settings.jwt_access_ttl_seconds,
        member=serialize_member(member, mfa_enabled=mfa_enabled),
        organization=serialize_org(org, viewer_role=member.role),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, request: Request, session: DbSession) -> Response:
    """Revoke the refresh family the cookie belongs to and clear the cookie.

    Origin-checked like `POST /auth/refresh`: the cookie is the only
    credential, so another site must not be able to sign the caller out.
    """
    _enforce_browser_origin(request)
    settings = get_settings()
    refresh_cookie = read_refresh_cookie(request, settings)
    if refresh_cookie:
        try:
            token_id, _ = parse_refresh_token(refresh_cookie)
            record = await session.get(RefreshToken, token_id)
            if record is not None:
                await _revoke_family(session, record, datetime.now(UTC))
                await session.commit()
        except TokenError:
            pass
    clear_refresh_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    member: CurrentMember, request: Request, session: DbSession
) -> list[SessionOut]:
    """Active refresh tokens for the caller, newest first."""
    settings = get_settings()
    current_id: UUID | None = None
    cookie = read_refresh_cookie(request, settings)
    if cookie:
        try:
            current_id, _ = parse_refresh_token(cookie)
        except TokenError:
            current_id = None

    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.member_id == member.member_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.rotated_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
        )
    ).all()
    return [
        SessionOut(
            id=str(row.id),
            created_at=row.created_at.isoformat(),
            last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
            user_agent=row.user_agent,
            ip_address=row.ip_address,
            current=row.id == current_id,
        )
        for row in rows
    ]


@router.post("/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(
    member: CurrentMember, response: Response, session: DbSession
) -> Response:
    """Sign the member out everywhere.

    Logging out only killed the device you were holding, which is useless when
    the reason you are logging out is that a different device was stolen.
    """
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.member_id == member.member_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="auth",
        action="sessions_revoked_all",
        metadata={},
    )
    await session.commit()
    clear_refresh_cookie(response, get_settings())
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/switch-org",
    response_model=AuthResponse | MfaChallengeResponse,
    response_model_exclude_none=True,
)
async def switch_org(
    payload: SwitchOrgRequest,
    member: CurrentMember,
    response: Response,
    request: Request,
    session: DbSession,
) -> AuthResponse | MfaChallengeResponse:
    """Move the session to another org the same email belongs to.

    Switching used to mean logging in again, so staff who cover two hospitals
    re-typed their password all day and learned to pick a weak one. An address
    on its own proves nothing, because anyone may sign up an organization
    naming somebody else: both the calling membership and the target membership
    must have a verified email, or the call is refused. A target membership
    with MFA enabled returns the same challenge as `POST /auth/login`, and the
    session is only issued once `POST /auth/mfa/verify` accepts a code.
    """
    caller = await session.get(OrgMember, member.member_id)
    target = await session.scalar(
        select(OrgMember).where(
            OrgMember.org_id == payload.org_id,
            OrgMember.email == member.email,
            OrgMember.status == "active",
        )
    )
    if caller is None or caller.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_SWITCH_DENIED_DETAIL)
    if target is None or target.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_SWITCH_DENIED_DETAIL)
    org = await session.get(Organization, target.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="organization missing")

    if await _mfa_enabled(session, target.id):
        await write_audit_event(
            session,
            org_id=org.id,
            actor=f"member:{member.email}",
            category="auth",
            action="org_switch_mfa_challenged",
            metadata={"from_org_id": str(member.org_id)},
        )
        await session.commit()
        return MfaChallengeResponse(
            challenge_token=issue_challenge_token(target.id, org.id),
            expires_in=CHALLENGE_TTL_SECONDS,
        )

    await write_audit_event(
        session,
        org_id=org.id,
        actor=f"member:{member.email}",
        category="auth",
        action="org_switched",
        metadata={"from_org_id": str(member.org_id)},
    )
    return await issue_session(session, response, target, org, request=request)


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
) -> Response:
    """Mail a single-use reset link.

    Always 204, whatever the address. Any difference in status, body or timing
    between a known and an unknown address turns this into a directory of who
    works at the hospital, so the token is minted before the lookup and the
    delivery is handed to a background task: both branches do the same work
    inside the request and neither waits on SMTP.
    """
    email = str(payload.email).lower().strip()
    limit = await login_rate_limiter.check(request, f"forgot:{email}")
    if not limit.allowed:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    settings = get_settings()
    issued = await asyncio.to_thread(
        issue_token,
        PASSWORD_RESET_PREFIX,
        ttl=timedelta(seconds=settings.password_reset_ttl_seconds),
    )
    member = await session.scalar(
        select(OrgMember)
        .where(OrgMember.email == email, OrgMember.status == "active")
        .order_by(OrgMember.created_at.asc())
        .limit(1)
    )
    if member is not None:
        session.add(
            PasswordResetToken(
                id=issued.id,
                member_id=member.id,
                token_hash=issued.token_hash,
                expires_at=issued.expires_at,
            )
        )
        await session.commit()
        subject, body = password_reset_email(
            base_url=settings.app_base_url,
            token=issued.raw_token,
            ttl_seconds=settings.password_reset_ttl_seconds,
        )
        background_tasks.add_task(deliver_quietly, to=member.email, subject=subject, body=body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def deliver_quietly(*, to: str, subject: str, body: str) -> None:
    await send_quietly(get_mailer(), to=to, subject=subject, body=body)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(payload: ResetPasswordRequest, session: DbSession) -> Response:
    now = datetime.now(UTC)
    try:
        token_id, secret = parse_token(PASSWORD_RESET_PREFIX, payload.token.strip())
    except TokenFormatError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL) from exc

    record = await session.get(PasswordResetToken, token_id)
    if record is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL)
    secret_ok = await asyncio.to_thread(verify_token_secret, secret, record.token_hash)
    if not secret_ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL)
    if record.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL)
    if record.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL)

    member = await session.get(OrgMember, record.member_id)
    if member is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_DETAIL)
    _enforce_password_policy(payload.password, member.email)

    password_hash = await asyncio.to_thread(hash_password, payload.password)
    record.used_at = now
    siblings = (
        await session.scalars(select(OrgMember).where(OrgMember.email == member.email))
    ).all()
    for sibling in siblings:
        sibling.password_hash = password_hash
        if sibling.status == "invited":
            sibling.status = "active"
            sibling.invite_token = None
            sibling.invite_expires_at = None
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.member_id.in_([sibling.id for sibling in siblings]),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="auth",
        action="password_reset",
        metadata={"member_id": str(member.id)},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(payload: VerifyEmailRequest, session: DbSession) -> Response:
    """Mark the membership the token was issued for as verified.

    Verification is per membership and never spreads to other organizations
    using the same address, because those memberships may have been created by
    somebody else who merely typed the address in.
    """
    now = datetime.now(UTC)
    try:
        token_id, secret = parse_token(EMAIL_VERIFY_PREFIX, payload.token.strip())
    except TokenFormatError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL) from exc

    record = await session.get(EmailVerificationToken, token_id)
    if record is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL)
    secret_ok = await asyncio.to_thread(verify_token_secret, secret, record.token_hash)
    if not secret_ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL)
    if record.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL)
    if record.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL)

    member = await session.get(OrgMember, record.member_id)
    if member is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=_VERIFY_TOKEN_DETAIL)

    record.used_at = now
    if member.email_verified_at is None:
        member.email_verified_at = now
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(member: CurrentMember, session: DbSession) -> Response:
    """Re-send the verification mail for the caller's own address.

    Authenticated rather than taking an email in the body: an open endpoint
    would both enumerate accounts and let anyone mail-bomb a staff address.
    """
    db_member = await session.get(OrgMember, member.member_id)
    if db_member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member missing")
    if db_member.email_verified_at is None:
        await _send_verification_email(session, db_member)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthResponse)
async def me(member: CurrentMember, session: DbSession) -> AuthResponse:
    org = await session.get(Organization, member.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="organization missing")
    db_member = await session.get(OrgMember, member.member_id)
    if db_member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member missing")
    return AuthResponse(
        access_token="",
        expires_in=0,
        member=serialize_member(db_member, mfa_enabled=await _mfa_enabled(session, db_member.id)),
        organization=serialize_org(org, viewer_role=db_member.role),
    )


class OrgListItem(BaseModel):
    id: str
    name: str
    slug: str
    role: str


@router.get("/my-orgs", response_model=list[OrgListItem])
async def my_orgs(member: CurrentMember, session: DbSession) -> list[OrgListItem]:
    """List the organizations the caller can switch into.

    Empty until the caller's own address is verified, and it lists only active
    memberships that are themselves verified, so an organization signed up in
    somebody else's name never appears in their switcher.
    """
    caller = await session.get(OrgMember, member.member_id)
    if caller is None or caller.email_verified_at is None:
        return []
    members = (
        await session.scalars(
            select(OrgMember).where(
                OrgMember.email == member.email,
                OrgMember.status == "active",
                OrgMember.email_verified_at.is_not(None),
            )
        )
    ).all()
    return await _org_choices(session, list(members))


async def _org_choices(session: AsyncSession, members: list[OrgMember]) -> list[OrgListItem]:
    results: list[OrgListItem] = []
    for m in members:
        org = await session.get(Organization, m.org_id)
        if org:
            results.append(
                OrgListItem(
                    id=str(org.id),
                    name=org.name,
                    slug=org.slug,
                    role=m.role,
                )
            )
    return results


async def _login_members(
    session: AsyncSession, email: str, org_slug: str | None
) -> list[OrgMember]:
    if org_slug:
        org_id = await session.scalar(
            select(Organization.id).where(Organization.slug == _slugify(org_slug))
        )
        if org_id is None:
            return []
        member = await session.scalar(
            select(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.email == email,
                OrgMember.status == "active",
            )
        )
        return [member] if member is not None else []

    rows = (
        await session.scalars(
            select(OrgMember).where(OrgMember.email == email, OrgMember.status == "active")
        )
    ).all()
    return list(rows)


async def _audit_login_lockout(
    session: AsyncSession,
    *,
    email: str,
    org_slug: str | None,
    retry_after_seconds: int,
) -> None:
    await _audit_login(
        session,
        members=await _login_members(session, email, org_slug),
        email=email,
        action="login_rate_limited",
        metadata={"retry_after_seconds": retry_after_seconds},
    )


async def _audit_login(
    session: AsyncSession,
    *,
    members: list[OrgMember],
    email: str,
    action: str,
    metadata: dict[str, object] | None = None,
) -> None:
    seen_orgs: set[UUID] = set()
    for member in members:
        if member.org_id in seen_orgs:
            continue
        seen_orgs.add(member.org_id)
        await write_audit_event(
            session,
            org_id=member.org_id,
            actor=f"login:{email}",
            category="auth",
            action=action,
            metadata={"email": email, **(metadata or {})},
        )
    if seen_orgs:
        await session.commit()


async def _claim_rotation(session: AsyncSession, record: RefreshToken, now: datetime) -> bool:
    claimed = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.rotated_at.is_(None))
        .values(rotated_at=now)
        .returning(RefreshToken.id)
        .execution_options(synchronize_session=False)
    )
    return claimed.scalar_one_or_none() is not None


async def _revoke_family(session: AsyncSession, record: RefreshToken, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == record.family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def _send_verification_email(session: AsyncSession, member: OrgMember) -> None:
    settings = get_settings()
    issued = await asyncio.to_thread(
        issue_token,
        EMAIL_VERIFY_PREFIX,
        ttl=timedelta(hours=settings.email_verification_ttl_hours),
    )
    session.add(
        EmailVerificationToken(
            id=issued.id,
            member_id=member.id,
            token_hash=issued.token_hash,
            expires_at=issued.expires_at,
        )
    )
    subject, body = email_verification_email(
        base_url=settings.app_base_url,
        token=issued.raw_token,
        ttl_hours=settings.email_verification_ttl_hours,
    )
    await send_quietly(get_mailer(), to=member.email, subject=subject, body=body)
