from __future__ import annotations

import asyncio
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.audit.service import write_audit_event
from blade_backend.auth.passwords import PasswordPolicyError, hash_password, validate_password
from blade_backend.auth.tokens import SHA256_HASH_PREFIX, hash_token_secret_text
from blade_backend.db.models import DeviceToken, MemberMfa, MemberRole, Organization, OrgMember
from blade_backend.deps import CurrentMember, DbSession, OwnerOrAdmin, Principal
from blade_backend.email import get_mailer, send_quietly
from blade_backend.email.templates import invite_email
from blade_backend.settings import get_settings

router = APIRouter()

INVITE_TTL_DAYS = 7

_INVITE_TOKEN_BYTES = 32


class MemberOut(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    status: str
    last_login_at: str | None
    created_at: str
    email_verified: bool
    mfa_enabled: bool


class InviteRequest(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    role: MemberRole = "viewer"


class InviteResponse(BaseModel):
    member: MemberOut
    email_sent: bool


class AcceptInviteResponse(BaseModel):
    member: MemberOut
    org_slug: str


class AcceptInvite(BaseModel):
    invite_token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=12, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class RoleUpdate(BaseModel):
    role: MemberRole


def _serialize(member: OrgMember, *, mfa_enabled: bool = False) -> MemberOut:
    return MemberOut(
        id=str(member.id),
        email=member.email,
        name=member.name,
        role=member.role,
        status=member.status,
        last_login_at=member.last_login_at.isoformat() if member.last_login_at else None,
        created_at=member.created_at.isoformat(),
        email_verified=member.email_verified_at is not None,
        mfa_enabled=mfa_enabled,
    )


async def _member_for_invite(session: AsyncSession, submitted: str) -> OrgMember | None:
    hashed = hash_token_secret_text(submitted)
    member = await session.scalar(select(OrgMember).where(OrgMember.invite_token == hashed))
    if member is None:
        member = await session.scalar(select(OrgMember).where(OrgMember.invite_token == submitted))
    if member is None or member.invite_token is None:
        return None
    stored = member.invite_token
    expected = hashed if stored.startswith(SHA256_HASH_PREFIX) else submitted
    return member if hmac.compare_digest(stored, expected) else None


async def _owner_count(session: AsyncSession, org_id: UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(OrgMember)
        .where(
            OrgMember.org_id == org_id,
            OrgMember.status == "active",
            OrgMember.role == "owner",
        )
    )
    return int(count or 0)


async def _assert_can_change_owner_status(
    session: AsyncSession, actor: Principal, target: OrgMember, new_role: MemberRole | None = None
) -> None:
    if target.role == "owner" and actor.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="only owners can modify owners")
    if new_role == "owner" and actor.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="only owners can grant owner role")
    if (
        target.role == "owner"
        and new_role is not None
        and new_role != "owner"
        and await _owner_count(session, target.org_id) <= 1
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot remove last owner")


@router.get("", response_model=list[MemberOut])
async def list_members(member: CurrentMember, session: DbSession) -> list[MemberOut]:
    rows = (
        await session.scalars(
            select(OrgMember)
            .where(OrgMember.org_id == member.org_id)
            .order_by(OrgMember.created_at.asc())
        )
    ).all()
    enrolled = set(
        (
            await session.scalars(
                select(MemberMfa.member_id).where(
                    MemberMfa.member_id.in_([row.id for row in rows]),
                    MemberMfa.confirmed_at.is_not(None),
                )
            )
        ).all()
    )
    return [_serialize(row, mfa_enabled=row.id in enrolled) for row in rows]


@router.post("/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    payload: InviteRequest,
    member: CurrentMember,
    session: DbSession,
    _admin: OwnerOrAdmin,
) -> InviteResponse:
    if payload.role == "owner" and member.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="only owners can invite owners")

    email = str(payload.email).lower().strip()
    existing = await session.scalar(
        select(OrgMember.id).where(OrgMember.org_id == member.org_id, OrgMember.email == email)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="member already exists")

    invite_token = secrets.token_urlsafe(_INVITE_TOKEN_BYTES)
    new_member = OrgMember(
        org_id=member.org_id,
        email=email,
        name=payload.name,
        role=payload.role,
        status="invited",
        invite_token=hash_token_secret_text(invite_token),
        invite_expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(new_member)
    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="members",
        action="member_invited",
        metadata={"email": email, "role": payload.role},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="member already exists") from exc
    await session.refresh(new_member)

    org = await session.get(Organization, member.org_id)
    subject, body = invite_email(
        base_url=get_settings().app_base_url,
        org_name=org.name if org else "your organization",
        inviter_email=member.email,
        token=invite_token,
    )
    email_sent = await send_quietly(get_mailer(), to=email, subject=subject, body=body)
    return InviteResponse(member=_serialize(new_member), email_sent=email_sent)


@router.post("/accept-invite", response_model=AcceptInviteResponse)
async def accept_invite(payload: AcceptInvite, session: DbSession) -> AcceptInviteResponse:
    """Turn an invitation into an active membership with a password.

    Invitations are stored as a sha256 of the emailed token, so a copy of the
    members table is not a set of working invitations. Rows written before
    that change still hold the token itself; those are accepted once and the
    column is cleared by acceptance.
    """
    member = await _member_for_invite(session, payload.invite_token.strip())
    if member is None or member.status != "invited":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invite not found")
    if member.invite_expires_at and member.invite_expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, detail="invite expired")
    org = await session.get(Organization, member.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="organization missing")

    try:
        validate_password(payload.password, email=member.email)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    member.password_hash = await asyncio.to_thread(hash_password, payload.password)
    if payload.name:
        member.name = payload.name
    member.status = "active"
    member.invite_token = None
    member.invite_expires_at = None
    member.email_verified_at = datetime.now(UTC)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="invite already accepted") from exc
    await session.refresh(member)
    return AcceptInviteResponse(member=_serialize(member), org_slug=org.slug)


@router.patch("/{member_id}", response_model=MemberOut)
async def update_member_role(
    member_id: UUID,
    payload: RoleUpdate,
    member: CurrentMember,
    session: DbSession,
    _admin: OwnerOrAdmin,
) -> MemberOut:
    target = await session.get(OrgMember, member_id)
    if target is None or target.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found")
    if target.id == member.member_id and payload.role != "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot demote self")
    await _assert_can_change_owner_status(session, member, target, payload.role)

    previous_role = target.role
    target.role = payload.role
    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="members",
        action="member_role_changed",
        metadata={
            "member_id": str(target.id),
            "email": target.email,
            "from_role": previous_role,
            "to_role": payload.role,
        },
    )
    await session.commit()
    await session.refresh(target)
    return _serialize(target)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: UUID,
    member: CurrentMember,
    session: DbSession,
    _admin: OwnerOrAdmin,
) -> None:
    """Remove a member and revoke every device token enrolled in their name.

    The device row survives the member with a null `member_id`, so without an
    explicit revocation the extension on the leaver's laptop would keep
    scanning and keep resolving to the organization.
    """
    target = await session.get(OrgMember, member_id)
    if target is None or target.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="member not found")
    if target.id == member.member_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot remove self")
    await _assert_can_change_owner_status(session, member, target)
    if target.role == "owner" and await _owner_count(session, target.org_id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cannot remove last owner")

    revoked = await revoke_member_devices(session, actor_email=member.email, target=target)
    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="members",
        action="member_removed",
        metadata={
            "member_id": str(target.id),
            "email": target.email,
            "role": target.role,
            "devices_revoked": revoked,
        },
    )
    await session.delete(target)
    await session.commit()


async def revoke_member_devices(
    session: AsyncSession, *, actor_email: str, target: OrgMember
) -> int:
    result = await session.execute(
        update(DeviceToken)
        .where(DeviceToken.member_id == target.id, DeviceToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .returning(DeviceToken.id)
    )
    device_ids = [str(device_id) for device_id in result.scalars().all()]
    if device_ids:
        await write_audit_event(
            session,
            org_id=target.org_id,
            actor=f"member:{actor_email}",
            category="device",
            action="devices_revoked_with_member",
            metadata={"member_id": str(target.id), "device_ids": device_ids},
        )
    return len(device_ids)
