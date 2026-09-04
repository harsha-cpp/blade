from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from blade_backend.api.pagination import decode_cursor, encode_cursor
from blade_backend.audit.service import write_audit_event
from blade_backend.db.models import AccessRequest, AccessRequestStatus
from blade_backend.deps import DbSession, PlatformAdmin

router = APIRouter()

MAX_PAGE_SIZE = 200


class AccessRequestOut(BaseModel):
    id: str
    created_at: str
    organization: str
    name: str
    email: str
    phone: str | None
    role: str | None
    size: str | None
    message: str | None
    status: AccessRequestStatus
    notes: str | None
    handled_at: str | None
    handled_by: str | None


def _empty_items() -> list[AccessRequestOut]:
    return []


class AccessRequestPage(BaseModel):
    items: list[AccessRequestOut] = Field(default_factory=_empty_items)
    next_cursor: str | None = None


class AccessRequestPatch(BaseModel):
    status: AccessRequestStatus | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=2000)


def _serialize(row: AccessRequest) -> AccessRequestOut:
    return AccessRequestOut(
        id=str(row.id),
        created_at=row.created_at.isoformat(),
        organization=row.organization,
        name=row.name,
        email=row.email,
        phone=row.phone,
        role=row.role,
        size=row.size,
        message=row.message,
        status=row.status,
        notes=row.notes,
        handled_at=row.handled_at.isoformat() if row.handled_at else None,
        handled_by=row.handled_by,
    )


@router.get("", response_model=AccessRequestPage)
async def list_access_requests(
    _operator: PlatformAdmin,
    session: DbSession,
    status_filter: Annotated[AccessRequestStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> AccessRequestPage:
    """List marketing-site access requests, newest first.

    Platform operators only: the rows belong to no organization, so they are
    gated on PLATFORM_ADMIN_EMAILS rather than on a per-org role. Keyset
    paged on (created_at, id); pass `next_cursor` back as `cursor`.
    """
    statement = select(AccessRequest)
    if status_filter is not None:
        statement = statement.where(AccessRequest.status == status_filter)
    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                AccessRequest.created_at < cursor_time,
                (AccessRequest.created_at == cursor_time) & (AccessRequest.id < cursor_id),
            )
        )

    rows = list(
        (
            await session.scalars(
                statement.order_by(AccessRequest.created_at.desc(), AccessRequest.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return AccessRequestPage(
        items=[_serialize(row) for row in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None,
    )


@router.patch("/{request_id}", response_model=AccessRequestOut)
async def update_access_request(
    request_id: UUID,
    payload: AccessRequestPatch,
    operator: PlatformAdmin,
    session: DbSession,
) -> AccessRequestOut:
    """Move a request through triage and leave a note.

    The audit event lands in the operator's own organization, which is the only
    org a platform operator is a member of.
    """
    row = await session.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="access request not found")

    previous_status = row.status
    if payload.status is not None:
        row.status = payload.status
        row.handled_at = datetime.now(UTC)
        row.handled_by = operator.email
    if payload.notes is not None:
        row.notes = payload.notes

    await write_audit_event(
        session,
        org_id=operator.org_id,
        actor=f"member:{operator.email}",
        category="access_request",
        action="access_request_updated",
        metadata={
            "access_request_id": str(row.id),
            "from_status": previous_status,
            "to_status": row.status,
        },
    )
    await session.commit()
    await session.refresh(row)
    return _serialize(row)
