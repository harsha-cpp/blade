from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.audit.service import build_event_hash, row_hmac_for, verify_chain
from blade_backend.db.models import AuditEvent


class _Scalars:
    def __init__(self, rows: list[AuditEvent]) -> None:
        self._rows = rows

    def all(self) -> list[AuditEvent]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[AuditEvent], batch_size: int = 2) -> None:
        self._rows = rows
        self._batch_size = batch_size
        self.batches = 0

    async def scalars(self, statement: Select[tuple[AuditEvent]]) -> _Scalars:
        self.batches += 1
        after_seq = int(cast(int, statement.compile().params.get("seq_1", 0)))
        remaining = [row for row in self._rows if row.seq > after_seq]
        return _Scalars(remaining[: self._batch_size])


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(Any, session))


def _chain(org_id: UUID, length: int, *, sign: bool = True) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    base = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    for index in range(length):
        created_at = base + timedelta(seconds=index)
        metadata: dict[str, object] = {"step": index}
        event_hash = build_event_hash(
            org_id=org_id,
            actor="member:dr@hospital.in",
            category="quarantine",
            action="approved",
            metadata=metadata,
            previous_hash=previous_hash,
            created_at=created_at,
        )
        event = AuditEvent(
            id=uuid4(),
            org_id=org_id,
            actor="member:dr@hospital.in",
            category="quarantine",
            action="approved",
            metadata_json=metadata,
            previous_hash=previous_hash,
            event_hash=event_hash,
            seq=index + 1,
            created_at=created_at,
        )
        event.row_hmac = row_hmac_for(event) if sign else None
        events.append(event)
        previous_hash = event_hash
    return events


def _resequence(events: list[AuditEvent]) -> list[AuditEvent]:
    for position, event in enumerate(events):
        event.seq = position + 1
        if event.row_hmac is not None:
            event.row_hmac = row_hmac_for(event)
    return events


@pytest.mark.unit
async def test_intact_chain_verifies() -> None:
    org_id = uuid4()

    result = await verify_chain(_as_session(_FakeSession(_chain(org_id, 5))), org_id)

    assert result.ok
    assert result.checked == 5
    assert result.legacy == 0


@pytest.mark.unit
async def test_a_long_chain_is_read_in_batches() -> None:
    org_id = uuid4()
    session = _FakeSession(_chain(org_id, 7), batch_size=2)

    result = await verify_chain(_as_session(session), org_id)

    assert result.ok
    assert result.checked == 7
    assert session.batches == 5


@pytest.mark.unit
async def test_empty_chain_verifies() -> None:
    result = await verify_chain(_as_session(_FakeSession([])), uuid4())

    assert result.ok
    assert result.checked == 0


@pytest.mark.unit
async def test_edited_row_is_caught_at_its_position() -> None:
    org_id = uuid4()
    events = _chain(org_id, 5)
    events[2].actor = "member:attacker@hospital.in"

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.position == 2
    assert result.broken.event_id == events[2].id
    assert result.broken.kind == "chain"
    assert "event_hash" in result.broken.reason


@pytest.mark.unit
async def test_deleted_row_breaks_the_link() -> None:
    org_id = uuid4()
    events = _chain(org_id, 5)
    del events[2]
    _resequence(events)

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.position == 2
    assert result.broken.kind == "chain"
    assert "previous_hash" in result.broken.reason


@pytest.mark.unit
async def test_a_gap_in_the_sequence_is_caught() -> None:
    org_id = uuid4()
    events = _chain(org_id, 3)
    del events[1]

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.position == 1
    assert result.broken.kind == "chain"
    assert "seq" in result.broken.reason


@pytest.mark.unit
async def test_forked_chain_is_caught() -> None:
    org_id = uuid4()
    events = _chain(org_id, 3)
    fork = _chain(org_id, 2)[1]
    fork.created_at = events[2].created_at + timedelta(seconds=1)
    events.append(fork)
    _resequence(events)

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.position == 3


@pytest.mark.unit
async def test_a_tampered_row_hmac_is_reported_as_an_hmac_failure() -> None:
    org_id = uuid4()
    events = _chain(org_id, 4)
    events[1].row_hmac = "0" * 64

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.kind == "row_hmac"
    assert result.broken.position == 1
    assert result.broken.seq == 2
    assert "row_hmac" in result.broken.reason


@pytest.mark.unit
async def test_unsigned_rows_before_the_migration_are_legacy_not_broken() -> None:
    org_id = uuid4()
    events = _chain(org_id, 5)
    for event in events[:3]:
        event.row_hmac = None

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.ok
    assert result.checked == 5
    assert result.legacy == 3


@pytest.mark.unit
async def test_a_wholly_unsigned_chain_is_all_legacy() -> None:
    org_id = uuid4()

    result = await verify_chain(_as_session(_FakeSession(_chain(org_id, 3, sign=False))), org_id)

    assert result.ok
    assert result.legacy == 3


@pytest.mark.unit
async def test_a_stripped_hmac_after_the_migration_watermark_is_broken() -> None:
    org_id = uuid4()
    events = _chain(org_id, 4)
    events[0].row_hmac = None
    events[3].row_hmac = None

    result = await verify_chain(_as_session(_FakeSession(events)), org_id)

    assert result.broken is not None
    assert result.broken.kind == "row_hmac"
    assert result.broken.position == 3
    assert "missing" in result.broken.reason
