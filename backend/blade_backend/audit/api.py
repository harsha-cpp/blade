from __future__ import annotations

import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, or_, select

from blade_backend.api.pagination import decode_cursor, encode_cursor
from blade_backend.audit.service import BreakKind, verify_chain
from blade_backend.db.models import AuditEvent
from blade_backend.deps import CurrentMember, DbSession, OwnerOrAdmin
from blade_backend.observability.redis_gate import RedisGate

router = APIRouter()

MAX_PAGE_SIZE = 200
CHAIN_COOLDOWN_SECONDS = 60
MAX_CACHED_ORGS = 5_000
LIKE_ESCAPE = "\\"


class AuditEventOut(BaseModel):
    id: str
    actor: str
    category: str
    action: str
    metadata: dict[str, object]
    previous_hash: str | None
    event_hash: str
    created_at: str


def _empty_events() -> list[AuditEventOut]:
    return []


class AuditPage(BaseModel):
    events: list[AuditEventOut] = Field(default_factory=_empty_events)
    next_cursor: str | None = None


class ChainStatus(BaseModel):
    ok: bool
    checked: int
    legacy: int = 0
    broken_at: int | None = None
    failed_check: BreakKind | None = None
    detail: str | None = None
    verified_at: str
    cached: bool = False


class ChainStatusCache:
    def __init__(self) -> None:
        self._redis = RedisGate()
        self._entries: OrderedDict[UUID, tuple[float, str]] = OrderedDict()

    async def get(self, org_id: UUID) -> ChainStatus | None:
        payload = await self._read(org_id)
        if payload is None:
            return None
        return ChainStatus.model_validate_json(payload).model_copy(update={"cached": True})

    async def put(self, org_id: UUID, status: ChainStatus) -> None:
        payload = status.model_dump_json()
        redis = await self._redis.client()
        if redis is not None:
            try:
                await redis.set(_chain_key(org_id), payload, ex=CHAIN_COOLDOWN_SECONDS)
                return
            except Exception:
                self._redis.drop()
        self._entries[org_id] = (time.monotonic(), payload)
        self._entries.move_to_end(org_id)
        while len(self._entries) > MAX_CACHED_ORGS:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    async def _read(self, org_id: UUID) -> str | None:
        redis = await self._redis.client()
        if redis is not None:
            try:
                stored = await redis.get(_chain_key(org_id))
                return str(stored) if stored is not None else None
            except Exception:
                self._redis.drop()
        entry = self._entries.get(org_id)
        if entry is None:
            return None
        if time.monotonic() - entry[0] >= CHAIN_COOLDOWN_SECONDS:
            self._entries.pop(org_id, None)
            return None
        return entry[1]


def _chain_key(org_id: UUID) -> str:
    return f"auditchain:{org_id}"


chain_status_cache = ChainStatusCache()


def escape_like(value: str) -> str:
    return value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", "\\%").replace("_", "\\_")


def serialize_audit_event(event: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=str(event.id),
        actor=event.actor,
        category=event.category,
        action=event.action,
        metadata=event.metadata_json,
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
        created_at=event.created_at.isoformat(),
    )


def _apply_filters(
    statement: Select[tuple[AuditEvent]],
    *,
    category: str | None,
    actor: str | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    search: str | None,
) -> Select[tuple[AuditEvent]]:
    if category:
        statement = statement.where(AuditEvent.category == category)
    if actor:
        statement = statement.where(AuditEvent.actor.ilike(f"%{actor.strip()}%"))
    if action:
        statement = statement.where(AuditEvent.action == action)
    if since:
        statement = statement.where(AuditEvent.created_at >= since)
    if until:
        statement = statement.where(AuditEvent.created_at <= until)
    if search:
        needle = f"%{escape_like(search.strip())}%"
        statement = statement.where(
            or_(
                AuditEvent.actor.ilike(needle, escape=LIKE_ESCAPE),
                AuditEvent.category.ilike(needle, escape=LIKE_ESCAPE),
                AuditEvent.action.ilike(needle, escape=LIKE_ESCAPE),
                AuditEvent.event_hash.ilike(needle, escape=LIKE_ESCAPE),
            )
        )
    return statement


@router.get("", response_model=AuditPage)
async def list_audit_events(
    member: CurrentMember,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    actor: Annotated[str | None, Query(max_length=200)] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
) -> AuditPage:
    statement = select(AuditEvent).where(AuditEvent.org_id == member.org_id)
    statement = _apply_filters(
        statement,
        category=category,
        actor=actor,
        action=action,
        since=since,
        until=until,
        search=search,
    )

    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                AuditEvent.created_at < cursor_time,
                (AuditEvent.created_at == cursor_time) & (AuditEvent.id < cursor_id),
            )
        )

    rows = list(
        (
            await session.scalars(
                statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    return AuditPage(
        events=[serialize_audit_event(event) for event in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None,
    )


@router.get("/categories", response_model=list[str])
async def list_audit_categories(member: CurrentMember, session: DbSession) -> list[str]:
    """Distinct categories, so the filter offers what actually exists."""
    rows = await session.scalars(
        select(AuditEvent.category)
        .where(AuditEvent.org_id == member.org_id)
        .distinct()
        .order_by(AuditEvent.category.asc())
    )
    return list(rows.all())


@router.get("/chain", response_model=ChainStatus)
async def verify_audit_chain(member: OwnerOrAdmin, session: DbSession) -> ChainStatus:
    """Walk the whole chain server-side, in sequence-ordered batches.

    The dashboard could previously only check continuity within the page it had
    loaded, which is not a tamper-evidence claim worth making. A full walk reads
    every row of the log, so one organisation gets one real verification a
    minute and repeat callers are served the result of that walk.
    """
    cached = await chain_status_cache.get(member.org_id)
    if cached is not None:
        return cached

    result = await verify_chain(session, member.org_id)
    broken = result.broken
    status = ChainStatus(
        ok=result.ok,
        checked=result.checked,
        legacy=result.legacy,
        broken_at=broken.position if broken else None,
        failed_check=broken.kind if broken else None,
        detail=broken.reason if broken else None,
        verified_at=datetime.now(UTC).isoformat(),
    )
    await chain_status_cache.put(member.org_id, status)
    return status
