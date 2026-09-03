from __future__ import annotations

import hashlib
import uuid
from typing import Any
from uuid import UUID

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.db.models import AttachmentScan, Organization
from blade_backend.storage import BlobNotFoundError, get_store
from blade_backend.tasks import scan_tasks
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

PAYLOAD = b"Ward rota for next week. Nothing sensitive in here at all."


class _FakeRequest:
    def __init__(self, retries: int) -> None:
        self.retries = retries


class _RetryScheduledError(Exception):
    pass


class _FakeTask:
    def __init__(self, retries: int) -> None:
        self.request = _FakeRequest(retries)
        self.countdown: int | None = None

    def retry(self, *, exc: BaseException, countdown: int) -> BaseException:
        del exc
        self.countdown = countdown
        return _RetryScheduledError()


async def _signup(client: AsyncClient) -> str:
    suffix = uuid.uuid4().hex[:10]
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"Test Hospital {suffix}",
            "email": f"owner-{suffix}@sunrisehospital.in",
            "password": "correct-horse-battery-staple-9",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["organization"]["org_code"])


async def _queued_row(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> tuple[UUID, str]:
    org_code = await _signup(api_client)
    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    assert org_id is not None
    storage_key = get_store().put(PAYLOAD, suffix=".txt")
    row = AttachmentScan(
        org_id=org_id,
        client_scan_id=uuid.uuid4().hex,
        attachment_id=uuid.uuid4().hex,
        filename="rota.txt",
        mime_type="text/plain",
        size_bytes=len(PAYLOAD),
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        status="queued",
        severity="none",
        risk_score=0,
        entities=[],
        extraction_errors=[],
        storage_path=storage_key,
    )
    db_session.add(row)
    await db_session.commit()
    return row.id, storage_key


async def _reload(db_session: AsyncSession, scan_id: UUID) -> AttachmentScan:
    db_session.expire_all()
    row = await db_session.get(AttachmentScan, scan_id)
    assert row is not None
    return row


async def test_a_transient_failure_retries_and_keeps_the_blob(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_id, storage_key = await _queued_row(api_client, db_session)

    def _explode(data: bytes, row: AttachmentScan) -> Any:
        del data, row
        raise OSError("object store is unreachable")

    monkeypatch.setattr(scan_tasks, "_detect", _explode)
    task = _FakeTask(retries=0)

    with pytest.raises(_RetryScheduledError):
        scan_tasks.run_queued_scan(task, scan_id)

    assert task.countdown == scan_tasks.RETRY_BACKOFF_SECONDS
    assert get_store().get(storage_key) == PAYLOAD
    row = await _reload(db_session, scan_id)
    assert row.status == "queued"
    assert row.storage_path == storage_key


async def test_the_last_attempt_fails_the_row_and_releases_the_blob(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_id, storage_key = await _queued_row(api_client, db_session)

    def _explode(data: bytes, row: AttachmentScan) -> Any:
        del data, row
        raise OSError("object store is unreachable")

    monkeypatch.setattr(scan_tasks, "_detect", _explode)

    scan_tasks.run_queued_scan(_FakeTask(retries=scan_tasks.MAX_RETRIES), scan_id)

    row = await _reload(db_session, scan_id)
    assert row.status == "failed"
    assert row.error == scan_tasks.UNREADABLE_ERROR
    assert row.storage_path is None
    with pytest.raises(BlobNotFoundError):
        get_store().get(storage_key)


async def test_a_soft_time_limit_records_a_failed_row_instead_of_dying(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_id, storage_key = await _queued_row(api_client, db_session)

    def _hang(data: bytes, row: AttachmentScan) -> Any:
        del data, row
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(scan_tasks, "_detect", _hang)

    scan_tasks.run_attachment_scan(_FakeTask(retries=0), scan_id)

    row = await _reload(db_session, scan_id)
    assert row.status == "failed"
    assert row.error == scan_tasks.TIMED_OUT_ERROR
    assert row.storage_path is None
    with pytest.raises(BlobNotFoundError):
        get_store().get(storage_key)


async def test_a_redelivered_task_does_not_rescan_a_finished_row(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_id, _storage_key = await _queued_row(api_client, db_session)
    scan_tasks.run_attachment_scan(_FakeTask(retries=0), scan_id)
    first = await _reload(db_session, scan_id)
    assert first.status == "scanned"

    def _must_not_run(data: bytes, row: AttachmentScan) -> Any:
        del data, row
        raise AssertionError("a redelivered task rescanned a finished row")

    monkeypatch.setattr(scan_tasks, "_detect", _must_not_run)

    scan_tasks.run_attachment_scan(_FakeTask(retries=0), scan_id)

    second = await _reload(db_session, scan_id)
    assert second.status == "scanned"
