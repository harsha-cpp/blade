# pyright: reportUnknownMemberType=false, reportUntypedFunctionDecorator=false

from __future__ import annotations

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from blade_detection.api import detect_email
from blade_detection.models import Attachment, EmailPayload, ScanResult
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from blade_backend.db.models import AttachmentScan
from blade_backend.scan.attachment_status import status_for_extraction
from blade_backend.settings import get_settings
from blade_backend.storage import BlobNotFoundError, BlobStore, get_store
from blade_backend.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 15
MISSING_STORAGE_KEY_ERROR = "queued attachment missing storage key"
MISSING_BLOB_ERROR = "queued attachment blob is gone"
TIMED_OUT_ERROR = "queued attachment scan timed out"
UNREADABLE_ERROR = "queued attachment could not be scanned"


@lru_cache(maxsize=1)
def _sync_engine() -> Engine:
    return create_engine(get_settings().database_sync_url, pool_pre_ping=True)


@contextmanager
def _materialized(data: bytes, filename: str) -> Generator[Path]:
    settings = get_settings()
    settings.attachment_temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="wb",
        suffix=Path(filename).suffix[:20],
        prefix="queued-",
        dir=settings.attachment_temp_dir,
        delete=False,
    )
    path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        yield path
    finally:
        path.unlink(missing_ok=True)


def _detect(data: bytes, row: AttachmentScan) -> ScanResult:
    with _materialized(data, row.filename) as path:
        return detect_email(
            EmailPayload(
                attachments=[
                    Attachment(
                        id=row.attachment_id,
                        filename=row.filename,
                        mime_type=row.mime_type,
                        size_bytes=row.size_bytes,
                        sha256=row.sha256,
                        local_path=str(path),
                    )
                ]
            )
        )


def _apply_result(row: AttachmentScan, result: ScanResult) -> None:
    row.severity = result.severity
    row.risk_score = Decimal(str(result.risk_score))
    row.entities = [
        entity.model_dump(
            include={"type", "masked_value", "confidence", "source", "attachment_id"},
            exclude_none=True,
        )
        for entity in result.entities
    ]
    row.extraction_errors = result.extraction_errors
    row.status = status_for_extraction(
        extraction_errors=result.extraction_errors,
        entity_count=len(row.entities),
    )
    row.error = "; ".join(result.extraction_errors[:3]) if result.extraction_errors else None


def _release_blob(store: BlobStore, row: AttachmentScan) -> None:
    if row.storage_path:
        store.delete(row.storage_path)
    row.storage_path = None


def mark_terminal_failure(scan_id: UUID, message: str) -> None:
    store = get_store()
    with Session(_sync_engine()) as session:
        row = session.get(AttachmentScan, scan_id)
        if row is None or row.status != "queued":
            return
        row.status = "failed"
        row.error = message
        _release_blob(store, row)
        session.commit()


def run_queued_scan(task: Any, scan_id: UUID) -> None:
    store = get_store()
    with Session(_sync_engine()) as session:
        row = session.get(AttachmentScan, scan_id)
        if row is None:
            logger.warning("queued attachment scan row missing", scan_id=str(scan_id))
            return
        if row.status != "queued":
            return
        if not row.storage_path:
            row.status = "failed"
            row.error = MISSING_STORAGE_KEY_ERROR
            session.commit()
            return

        try:
            data = store.get(row.storage_path)
        except BlobNotFoundError:
            row.status = "failed"
            row.error = MISSING_BLOB_ERROR
            row.storage_path = None
            session.commit()
            return

        try:
            result = _detect(data, row)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            session.rollback()
            retries = int(getattr(task.request, "retries", 0) or 0)
            if retries < MAX_RETRIES:
                logger.warning(
                    "queued attachment scan failed, retrying",
                    scan_id=str(scan_id),
                    attempt=retries + 1,
                    reason=type(exc).__name__,
                )
                raise task.retry(
                    exc=exc,
                    countdown=RETRY_BACKOFF_SECONDS * (2**retries),
                ) from exc
            logger.warning(
                "queued attachment scan failed permanently",
                scan_id=str(scan_id),
                reason=type(exc).__name__,
                detail=str(exc)[:500],
            )
            mark_terminal_failure(scan_id, UNREADABLE_ERROR)
            return

        _apply_result(row, result)
        _release_blob(store, row)
        session.commit()


def run_attachment_scan(task: Any, scan_id: UUID) -> None:
    try:
        run_queued_scan(task, scan_id)
    except SoftTimeLimitExceeded:
        logger.warning("queued attachment scan hit the soft time limit", scan_id=str(scan_id))
        mark_terminal_failure(scan_id, TIMED_OUT_ERROR)


@celery_app.task(
    name="blade.scan.process_attachment",
    bind=True,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_BACKOFF_SECONDS,
    acks_late=True,
)
def process_attachment_scan(self: Any, attachment_scan_id: str) -> None:
    run_attachment_scan(self, UUID(attachment_scan_id))
