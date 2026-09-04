from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from blade_backend.audit.service import row_hmac_for, verify_chain, write_audit_event
from blade_backend.db.models import AuditEvent
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

CONCURRENT_WRITERS = 6


async def _signup(client: AsyncClient) -> tuple[str, uuid.UUID]:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Chained Hospital {suffix}",
            "email": f"owner-{suffix}@chainedhospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], uuid.UUID(body["organization"]["id"])


async def _add_domain(client: AsyncClient, token: str) -> None:
    response = await client.post(
        "/api/v1/domains",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "chainedhospital.in", "direction": "both", "classification": "internal"},
    )
    assert response.status_code in {200, 201}, response.text


async def _seqs(session: AsyncSession, org_id: uuid.UUID) -> list[int]:
    rows = await session.scalars(
        select(AuditEvent.seq).where(AuditEvent.org_id == org_id).order_by(AuditEvent.seq.asc())
    )
    return list(rows.all())


async def test_concurrent_writers_produce_a_gapless_sequence(
    api_client: AsyncClient,
    db_session: AsyncSession,
    database_url: str,
) -> None:
    _token, org_id = await _signup(api_client)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _write(index: int) -> None:
        async with factory() as session:
            await write_audit_event(
                session,
                org_id=org_id,
                actor=f"member:writer-{index}@chainedhospital.in",
                category="test",
                action="concurrent_write",
                metadata={"writer": index},
            )
            await session.commit()

    try:
        await asyncio.gather(*(_write(index) for index in range(CONCURRENT_WRITERS)))
    finally:
        await engine.dispose()

    seqs = await _seqs(db_session, org_id)
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(seqs) >= CONCURRENT_WRITERS


async def test_a_concurrently_written_chain_still_verifies(
    api_client: AsyncClient,
    db_session: AsyncSession,
    database_url: str,
) -> None:
    _token, org_id = await _signup(api_client)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _write(index: int) -> None:
        async with factory() as session:
            await write_audit_event(
                session,
                org_id=org_id,
                actor=f"member:writer-{index}@chainedhospital.in",
                category="test",
                action="concurrent_write",
            )
            await session.commit()

    try:
        await asyncio.gather(*(_write(index) for index in range(CONCURRENT_WRITERS)))
    finally:
        await engine.dispose()

    result = await verify_chain(db_session, org_id)

    assert result.ok, result.broken
    assert result.legacy == 0
    assert result.checked >= CONCURRENT_WRITERS


async def test_every_written_row_carries_a_row_hmac(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_id = await _signup(api_client)
    await _add_domain(api_client, token)

    rows = (await db_session.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id))).all()

    assert rows
    for row in rows:
        assert row.row_hmac == row_hmac_for(row)


async def test_truncating_the_audit_log_is_refused(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("TRUNCATE TABLE audit_events"))
            await connection.rollback()
    finally:
        await engine.dispose()


async def test_the_audit_log_still_refuses_updates_and_deletes(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("UPDATE audit_events SET actor = 'x'"))
            await connection.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("DELETE FROM audit_events"))
            await connection.rollback()
    finally:
        await engine.dispose()
