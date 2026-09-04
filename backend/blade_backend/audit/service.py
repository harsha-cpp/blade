from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import AuditEvent
from blade_backend.settings import get_settings

_ORG_CHAIN_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:org_id AS text), 0))")

VERIFY_BATCH_SIZE = 500

BreakKind = Literal["chain", "row_hmac"]

_CHAIN_LINK_BROKEN = "previous_hash does not match the preceding event"
_EVENT_HASH_BROKEN = "event_hash does not match the stored payload"
_SEQUENCE_BROKEN = "seq is not contiguous with the preceding event"
_ROW_HMAC_BROKEN = "row_hmac does not match the stored row"
_ROW_HMAC_MISSING = "row_hmac is missing on a row written after the signing migration"


@dataclass(frozen=True, slots=True)
class ChainBreak:
    event_id: UUID
    position: int
    seq: int
    kind: BreakKind
    reason: str


@dataclass(frozen=True, slots=True)
class ChainVerification:
    checked: int
    legacy: int
    broken: ChainBreak | None

    @property
    def ok(self) -> bool:
        return self.broken is None


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def build_event_hash(
    *,
    org_id: UUID,
    actor: str,
    category: str,
    action: str,
    metadata: dict[str, object],
    previous_hash: str | None,
    created_at: datetime,
) -> str:
    payload: dict[str, Any] = {
        "org_id": str(org_id),
        "actor": actor,
        "category": category,
        "action": action,
        "metadata": metadata,
        "previous_hash": previous_hash,
        "created_at": created_at.isoformat(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_row_hmac(
    *,
    org_id: UUID,
    seq: int,
    actor: str,
    category: str,
    action: str,
    metadata: dict[str, object],
    previous_hash: str | None,
    event_hash: str,
    created_at: datetime,
) -> str:
    payload: dict[str, Any] = {
        "org_id": str(org_id),
        "seq": seq,
        "actor": actor,
        "category": category,
        "action": action,
        "metadata": metadata,
        "previous_hash": previous_hash,
        "event_hash": event_hash,
        "created_at": created_at.isoformat(),
    }
    return hmac.new(
        get_settings().audit_hmac_key_value.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def row_hmac_for(event: AuditEvent) -> str:
    return build_row_hmac(
        org_id=event.org_id,
        seq=event.seq,
        actor=event.actor,
        category=event.category,
        action=event.action,
        metadata=event.metadata_json,
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
        created_at=event.created_at,
    )


async def write_audit_event(
    session: AsyncSession,
    *,
    org_id: UUID,
    actor: str,
    category: str,
    action: str,
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    await session.execute(_ORG_CHAIN_LOCK, {"org_id": str(org_id)})
    previous = await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )
    created_at = datetime.now(UTC)
    metadata_json = metadata or {}
    previous_hash = previous.event_hash if previous else None
    event_hash = build_event_hash(
        org_id=org_id,
        actor=actor,
        category=category,
        action=action,
        metadata=metadata_json,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    event = AuditEvent(
        org_id=org_id,
        actor=actor,
        category=category,
        action=action,
        metadata_json=metadata_json,
        previous_hash=previous_hash,
        event_hash=event_hash,
        seq=previous.seq + 1 if previous else 1,
        created_at=created_at,
    )
    event.row_hmac = row_hmac_for(event)
    session.add(event)
    return event


async def _batch(session: AsyncSession, org_id: UUID, after_seq: int) -> list[AuditEvent]:
    rows = await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id, AuditEvent.seq > after_seq)
        .order_by(AuditEvent.seq.asc())
        .limit(VERIFY_BATCH_SIZE)
    )
    return list(rows.all())


def _break(event: AuditEvent, position: int, kind: BreakKind, reason: str) -> ChainBreak:
    return ChainBreak(
        event_id=event.id,
        position=position,
        seq=event.seq,
        kind=kind,
        reason=reason,
    )


def _chain_break(
    event: AuditEvent,
    *,
    position: int,
    expected_previous: str | None,
    expected_seq: int,
) -> ChainBreak | None:
    if event.seq != expected_seq:
        return _break(event, position, "chain", _SEQUENCE_BROKEN)
    if event.previous_hash != expected_previous:
        return _break(event, position, "chain", _CHAIN_LINK_BROKEN)
    recomputed = build_event_hash(
        org_id=event.org_id,
        actor=event.actor,
        category=event.category,
        action=event.action,
        metadata=event.metadata_json,
        previous_hash=event.previous_hash,
        created_at=event.created_at,
    )
    if recomputed != event.event_hash:
        return _break(event, position, "chain", _EVENT_HASH_BROKEN)
    return None


def _row_hmac_break(
    event: AuditEvent,
    *,
    position: int,
    in_legacy_prefix: bool,
) -> ChainBreak | None:
    if event.row_hmac is None:
        if in_legacy_prefix:
            return None
        return _break(event, position, "row_hmac", _ROW_HMAC_MISSING)
    if not hmac.compare_digest(event.row_hmac, row_hmac_for(event)):
        return _break(event, position, "row_hmac", _ROW_HMAC_BROKEN)
    return None


def _inspect(
    event: AuditEvent,
    *,
    position: int,
    expected_previous: str | None,
    expected_seq: int,
    in_legacy_prefix: bool,
) -> ChainBreak | None:
    broken = _chain_break(
        event,
        position=position,
        expected_previous=expected_previous,
        expected_seq=expected_seq,
    )
    if broken is not None:
        return broken
    return _row_hmac_break(event, position=position, in_legacy_prefix=in_legacy_prefix)


async def verify_chain(session: AsyncSession, org_id: UUID) -> ChainVerification:
    expected_previous: str | None = None
    position = 0
    legacy = 0
    in_legacy_prefix = True
    after_seq = 0

    while True:
        rows = await _batch(session, org_id, after_seq)
        if not rows:
            return ChainVerification(checked=position, legacy=legacy, broken=None)
        for event in rows:
            broken = _inspect(
                event,
                position=position,
                expected_previous=expected_previous,
                expected_seq=position + 1,
                in_legacy_prefix=in_legacy_prefix,
            )
            if broken is not None:
                return ChainVerification(checked=position, legacy=legacy, broken=broken)
            if event.row_hmac is None:
                legacy += 1
            else:
                in_legacy_prefix = False
            expected_previous = event.event_hash
            position += 1
        after_seq = rows[-1].seq
