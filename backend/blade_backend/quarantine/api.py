from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update

from blade_backend.api.pagination import decode_cursor, encode_cursor
from blade_backend.audit.service import write_audit_event
from blade_backend.db.models import QuarantineItem
from blade_backend.deps import CurrentMember, DbSession, Principal, QuarantineReviewer
from blade_backend.scan.credentials import principal_for_request
from blade_backend.scan.limits import enforce_scan_limit

router = APIRouter()

MAX_PAGE_SIZE = 200

QuarantineStatus = Literal["pending", "approved", "rejected"]
QuarantineStatusFilter = Literal["pending", "approved", "rejected", "all"]
_ALREADY_DECIDED_DETAIL = "quarantine already decided"
_NOT_FOUND_DETAIL = "quarantine item not found"


class QuarantineItemOut(BaseModel):
    id: str
    scan_id: str
    client_scan_id: str
    sender: str
    subject: str
    recipients: list[str]
    entities: list[dict[str, object]]
    matched_policy_ids: list[str]
    risk_score: float
    severity: str
    status: QuarantineStatus
    analyst_id: str | None
    analyst_note: str | None
    decided_at: str | None
    attachment_refs: list[dict[str, object]]
    created_at: str
    updated_at: str


class QuarantineDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _empty_items() -> list[QuarantineItemOut]:
    return []


class QuarantinePage(BaseModel):
    items: list[QuarantineItemOut] = Field(default_factory=_empty_items)
    next_cursor: str | None = None


class ExtensionQuarantineStatus(BaseModel):
    quarantine_id: str
    status: QuarantineStatus
    scan_id: str
    decided_at: str | None


def _serialize(item: QuarantineItem) -> QuarantineItemOut:
    return QuarantineItemOut(
        id=str(item.id),
        scan_id=item.scan_id,
        client_scan_id=item.client_scan_id,
        sender=item.sender,
        subject=item.subject,
        recipients=list(item.recipients or []),
        entities=list(item.entities or []),
        matched_policy_ids=list(item.matched_policy_ids or []),
        risk_score=float(item.risk_score),
        severity=item.severity,
        status=item.status,
        analyst_id=str(item.analyst_id) if item.analyst_id else None,
        analyst_note=item.analyst_note,
        decided_at=item.decided_at.isoformat() if item.decided_at else None,
        attachment_refs=list(item.attachment_refs or []),
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


async def _get_item(session: DbSession, item_id: UUID, org_id: UUID) -> QuarantineItem:
    item = await session.get(QuarantineItem, item_id)
    if item is None or item.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return item


async def _decide(
    session: DbSession,
    *,
    item_id: UUID,
    member: Principal,
    note: str | None,
    decision: QuarantineStatus,
) -> QuarantineItemOut:
    claimed = await session.execute(
        update(QuarantineItem)
        .where(
            QuarantineItem.id == item_id,
            QuarantineItem.org_id == member.org_id,
            QuarantineItem.status == "pending",
        )
        .values(
            status=decision,
            analyst_id=member.member_id,
            analyst_note=note,
            decided_at=datetime.now(UTC),
        )
        .returning(QuarantineItem)
        .execution_options(synchronize_session=False)
    )
    item = claimed.scalar_one_or_none()
    if item is None:
        await session.rollback()
        existing = await session.scalar(
            select(QuarantineItem.id).where(
                QuarantineItem.id == item_id, QuarantineItem.org_id == member.org_id
            )
        )
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_ALREADY_DECIDED_DETAIL)

    await write_audit_event(
        session,
        org_id=member.org_id,
        actor=f"member:{member.email}",
        category="quarantine",
        action=decision,
        metadata={"quarantine_id": str(item.id), "scan_id": item.scan_id},
    )
    await session.commit()
    await session.refresh(item)
    return _serialize(item)


@router.get("", response_model=QuarantinePage)
async def list_quarantine_items(
    member: CurrentMember,
    session: DbSession,
    status_filter: Annotated[QuarantineStatusFilter, Query(alias="status")] = "pending",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    cursor: Annotated[str | None, Query()] = None,
) -> QuarantinePage:
    """List quarantined messages, newest first.

    The response is an object, not a bare array: it is
    `{"items": [...], "next_cursor": str | null}`. Keyset paged on
    (created_at, id) exactly like the audit feed, so pass `next_cursor`
    straight back as `cursor` to walk backwards in time. A null `next_cursor`
    means the last page. Before this, `limit` was the only control and
    anything older than the newest 200 rows was unreachable.
    """
    statement = select(QuarantineItem).where(QuarantineItem.org_id == member.org_id)
    if status_filter != "all":
        statement = statement.where(QuarantineItem.status == status_filter)
    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                QuarantineItem.created_at < cursor_time,
                (QuarantineItem.created_at == cursor_time) & (QuarantineItem.id < cursor_id),
            )
        )

    rows = list(
        (
            await session.scalars(
                statement.order_by(
                    QuarantineItem.created_at.desc(),
                    QuarantineItem.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return QuarantinePage(
        items=[_serialize(item) for item in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None,
    )


@router.get("/{item_id}", response_model=QuarantineItemOut)
async def get_quarantine_item(
    item_id: UUID,
    member: CurrentMember,
    session: DbSession,
) -> QuarantineItemOut:
    return _serialize(await _get_item(session, item_id, member.org_id))


@router.post("/{item_id}/approve", response_model=QuarantineItemOut)
async def approve_quarantine_item(
    item_id: UUID,
    payload: QuarantineDecisionIn,
    member: CurrentMember,
    session: DbSession,
    _reviewer: QuarantineReviewer,
) -> QuarantineItemOut:
    """Release a held message.

    The decision is claimed with a conditional UPDATE, so when two reviewers
    press the button at once exactly one wins and the other gets a 409 instead
    of silently overwriting the first decision.
    """
    return await _decide(
        session, item_id=item_id, member=member, note=payload.note, decision="approved"
    )


@router.post("/{item_id}/reject", response_model=QuarantineItemOut)
async def reject_quarantine_item(
    item_id: UUID,
    payload: QuarantineDecisionIn,
    member: CurrentMember,
    session: DbSession,
    _reviewer: QuarantineReviewer,
) -> QuarantineItemOut:
    """Keep a held message blocked. Claimed the same way as approval."""
    return await _decide(
        session, item_id=item_id, member=member, note=payload.note, decision="rejected"
    )


@router.get("/{item_id}/status", response_model=ExtensionQuarantineStatus)
async def extension_quarantine_status(
    item_id: UUID,
    session: DbSession,
    org_code: Annotated[str | None, Query(min_length=3, max_length=128)] = None,
    x_blade_device_token: Annotated[str | None, Header()] = None,
) -> ExtensionQuarantineStatus:
    """Let the extension poll the decision on a message it had held.

    Takes either credential the scan path takes, so an enrolled install that no
    longer keeps the org code can still follow up its own quarantine. The item
    must belong to the caller's organisation, otherwise it is not found.
    """
    principal = await principal_for_request(session, org_code, x_blade_device_token)
    enforce_scan_limit(principal)
    item = await _get_item(session, item_id, principal.org_id)
    return ExtensionQuarantineStatus(
        quarantine_id=str(item.id),
        status=item.status,
        scan_id=item.scan_id,
        decided_at=item.decided_at.isoformat() if item.decided_at else None,
    )
