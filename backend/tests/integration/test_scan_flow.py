from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from typing import cast
from uuid import UUID

import pytest
from blade_detection.models import ScanResult
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

from blade_backend.db.models import (
    AttachmentScan,
    AuditEvent,
    Organization,
    QuarantineItem,
    ScanEvent,
)
from blade_backend.db.session import get_engine
from blade_backend.scan import api as scan_api
from blade_backend.scan.runner import DetectionTimeoutError
from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]

AADHAAR = "7534 7930 7460"
BODY_WITH_PHI = f"Patient Lakshmi Devi, UHID 0024518, diagnosis E11.9. Aadhaar {AADHAAR}."


async def _signup(client: AsyncClient) -> tuple[str, str]:
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
    payload = response.json()
    return payload["access_token"], payload["organization"]["org_code"]


async def _add_domain(
    client: AsyncClient,
    token: str,
    domain: str,
    classification: str,
    direction: str = "both",
) -> None:
    response = await client.post(
        "/api/v1/domains",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": domain, "classification": classification, "direction": direction},
    )
    assert response.status_code == 201, response.text


async def test_clean_message_is_allowed_and_recorded(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Cafeteria",
            "body": "Can we meet at noon to discuss the budget?",
            "recipients": ["colleague@sunrisehospital.in"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )
    assert response.status_code == 200, response.text
    verdict = response.json()
    assert verdict["action"] == "allow"
    assert verdict["risk_score"] == 0
    assert verdict["entities"] == []


async def test_phi_to_personal_gmail_is_quarantined_with_an_audit_trail(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": client_scan_id,
            "subject": "Discharge summary",
            "body": BODY_WITH_PHI,
            "recipients": ["patient@gmail.com"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )
    assert response.status_code == 200, response.text
    verdict = response.json()

    assert verdict["action"] in {"quarantine", "block"}
    assert verdict["risk_score"] > 50
    types = {entity["type"] for entity in verdict["entities"]}
    assert {"IN_AADHAAR", "MRN"} <= types
    assert AADHAAR.replace(" ", "") not in response.text
    assert "0024518" not in response.text

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    assert org_id is not None

    events = (await db_session.scalars(select(ScanEvent).where(ScanEvent.org_id == org_id))).all()
    assert len(events) == 1
    assert events[0].user_email == "doctor@sunrisehospital.in"

    audit = (await db_session.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id))).all()
    assert {row.category for row in audit} >= {"scan"}


async def test_sender_on_an_unapproved_domain_is_blocked(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Records",
            "body": BODY_WITH_PHI,
            "recipients": ["colleague@sunrisehospital.in"],
            "user_email": "doctor.personal@gmail.com",
        },
    )
    assert response.status_code == 200, response.text
    verdict = response.json()
    assert verdict["action"] == "block"
    assert "unapproved-sender-with-phi" in verdict["matched_policy_ids"]


async def test_blocked_recipient_domain_blocks(api_client: AsyncClient) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")
    await _add_domain(api_client, token, "competitorclinic.in", "blocked")

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Hello",
            "body": "Nothing sensitive here at all.",
            "recipients": ["someone@competitorclinic.in"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "block"


async def test_repeated_client_scan_id_does_not_duplicate_the_event(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex
    payload = {
        "org_code": org_code,
        "client_scan_id": client_scan_id,
        "subject": "Retry",
        "body": BODY_WITH_PHI,
        "recipients": ["patient@gmail.com"],
        "user_email": "doctor@sunrisehospital.in",
    }

    first = await api_client.post("/api/v1/scan/email", json=payload)
    second = await api_client.post("/api/v1/scan/email", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    events = (await db_session.scalars(select(ScanEvent).where(ScanEvent.org_id == org_id))).all()
    assert len(events) == 1, "a retried send must not double-count in analytics"


async def test_quarantined_message_appears_in_the_review_queue(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, org_code = await _signup(api_client)
    await _add_domain(api_client, token, "sunrisehospital.in", "internal")

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Discharge summary",
            "body": BODY_WITH_PHI,
            "recipients": ["patient@gmail.com"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )
    verdict = response.json()
    if verdict["action"] != "quarantine":
        pytest.skip("policy blocked rather than quarantined; covered elsewhere")

    assert verdict["quarantine_id"]
    listing = await api_client.get(
        "/api/v1/quarantine",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert any(item["id"] == verdict["quarantine_id"] for item in items)

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    stored = (
        await db_session.scalars(select(QuarantineItem).where(QuarantineItem.org_id == org_id))
    ).all()
    assert stored
    for item in stored:
        for entity in item.entities:
            assert AADHAAR.replace(" ", "") not in str(entity)


async def test_audit_events_reject_update_and_delete(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _token, org_code = await _signup(api_client)
    await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Discharge summary",
            "body": BODY_WITH_PHI,
            "recipients": ["patient@gmail.com"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )

    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    rows = (await db_session.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id))).all()
    assert rows, "the scan should have written audit events"

    with pytest.raises(Exception, match=r"(?i)append|immutable|update|not allowed"):
        await db_session.execute(
            text("UPDATE audit_events SET actor = 'tampered' WHERE org_id = :org"),
            {"org": org_id},
        )
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(Exception, match=r"(?i)append|immutable|delete|not allowed"):
        await db_session.execute(
            text("DELETE FROM audit_events WHERE org_id = :org"), {"org": org_id}
        )
        await db_session.commit()
    await db_session.rollback()


async def test_unknown_org_code_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": "BLD-DOESNOTEXIST",
            "client_scan_id": uuid.uuid4().hex,
            "subject": "",
            "body": "hello",
            "recipients": [],
        },
    )
    assert response.status_code == 404


async def _upload_attachment(
    client: AsyncClient,
    org_code: str,
    client_scan_id: str,
    filename: str,
    payload: bytes,
    mime_type: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/scan/attachment",
        data={
            "org_code": org_code,
            "client_scan_id": client_scan_id,
            "attachment_id": uuid.uuid4().hex,
        },
        files={"file": (filename, payload, mime_type)},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _finalize(
    client: AsyncClient,
    org_code: str,
    client_scan_id: str,
    attachment_scan_ids: list[str],
    body: str = "The paperwork is attached.",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/scan/finalize",
        json={
            "org_code": org_code,
            "client_scan_id": client_scan_id,
            "subject": "Paperwork",
            "body": body,
            "recipients": ["colleague@sunrisehospital.in"],
            "user_email": "doctor@sunrisehospital.in",
            "attachment_scan_ids": attachment_scan_ids,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _corrupt_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "this is not valid word xml")
    return buffer.getvalue()


def _archive_past_the_member_cap() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("summary.txt", BODY_WITH_PHI)
        for index in range(60):
            archive.writestr(f"filler-{index}.txt", "Ward rota, nothing sensitive.")
    return buffer.getvalue()


async def test_a_fully_scanned_clean_attachment_is_still_allowed(
    api_client: AsyncClient,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex

    uploaded = await _upload_attachment(
        api_client,
        org_code,
        client_scan_id,
        "rota.txt",
        b"Ward rota for next week. Nothing sensitive in here at all.",
        "text/plain",
    )
    assert uploaded["status"] == "scanned"

    verdict = await _finalize(
        api_client,
        org_code,
        client_scan_id,
        [str(uploaded["attachment_scan_id"])],
    )
    assert verdict["action"] == "allow"
    assert verdict["degraded"] is False


async def test_an_unreadable_attachment_is_degraded_and_not_allowed(
    api_client: AsyncClient,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex

    uploaded = await _upload_attachment(
        api_client,
        org_code,
        client_scan_id,
        "report.docx",
        _corrupt_docx(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert uploaded["status"] == "failed"
    assert uploaded["error"]

    verdict = await _finalize(
        api_client,
        org_code,
        client_scan_id,
        [str(uploaded["attachment_scan_id"])],
    )
    assert verdict["degraded"] is True
    assert verdict["action"] != "allow"
    matched_policy_ids = cast(list[str], verdict["matched_policy_ids"])
    assert "unscannable-attachment" in matched_policy_ids
    assert verdict["user_message"] == "One or more attachments could not be checked."


async def test_an_attachment_still_queued_at_finalize_is_degraded_and_not_allowed(
    api_client: AsyncClient,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex

    uploaded = await _upload_attachment(
        api_client,
        org_code,
        client_scan_id,
        "prescription.png",
        b"\x89PNG\r\n\x1a\nnot really an image",
        "image/png",
    )
    assert uploaded["status"] == "queued"

    verdict = await _finalize(
        api_client,
        org_code,
        client_scan_id,
        [str(uploaded["attachment_scan_id"])],
    )
    assert verdict["degraded"] is True
    assert verdict["action"] != "allow"
    matched_policy_ids = cast(list[str], verdict["matched_policy_ids"])
    assert "unscannable-attachment" in matched_policy_ids


async def test_a_partially_read_attachment_keeps_its_findings_and_is_degraded(
    api_client: AsyncClient,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")
    client_scan_id = uuid.uuid4().hex

    uploaded = await _upload_attachment(
        api_client,
        org_code,
        client_scan_id,
        "bundle.zip",
        _archive_past_the_member_cap(),
        "application/zip",
    )
    assert uploaded["status"] == "partial"

    verdict = await _finalize(
        api_client,
        org_code,
        client_scan_id,
        [str(uploaded["attachment_scan_id"])],
    )
    assert verdict["degraded"] is True
    assert verdict["action"] != "allow"
    entities = cast(list[dict[str, object]], verdict["entities"])
    types = {entity["type"] for entity in entities}
    assert "IN_AADHAAR" in types


def _checked_out_connections() -> int:
    return cast(QueuePool, get_engine().pool).checkedout()


async def _org_id(db_session: AsyncSession, org_code: str) -> UUID:
    org_id = await db_session.scalar(
        select(Organization.id).where(Organization.org_code == org_code)
    )
    assert org_id is not None
    return org_id


async def test_a_message_scan_past_its_deadline_is_degraded_and_not_allowed(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")

    async def _deadline_exceeded(subject: str, body: str, recipients: list[str]) -> ScanResult:
        del subject, body, recipients
        raise DetectionTimeoutError("detection exceeded its deadline")

    monkeypatch.setattr(scan_api, "scan_text", _deadline_exceeded)

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Cafeteria",
            "body": "Can we meet at noon to discuss the budget?",
            "recipients": ["colleague@sunrisehospital.in"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )

    assert response.status_code == 200, response.text
    verdict = response.json()
    assert verdict["degraded"] is True
    assert verdict["action"] != "allow"
    matched_policy_ids = cast(list[str], verdict["matched_policy_ids"])
    assert "unscannable-attachment" in matched_policy_ids


async def test_the_scan_path_releases_its_database_connection_before_detecting(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, org_code = await _signup(api_client)
    await _add_domain(api_client, _token, "sunrisehospital.in", "internal")
    original = scan_api.scan_text
    checked_out: list[int] = []

    async def _observe(subject: str, body: str, recipients: list[str]) -> ScanResult:
        checked_out.append(_checked_out_connections())
        return await original(subject, body, recipients)

    monkeypatch.setattr(scan_api, "scan_text", _observe)

    response = await api_client.post(
        "/api/v1/scan/email",
        json={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "subject": "Discharge summary",
            "body": BODY_WITH_PHI,
            "recipients": ["patient@gmail.com"],
            "user_email": "doctor@sunrisehospital.in",
        },
    )

    assert response.status_code == 200, response.text
    assert checked_out == [0], "detection must not run while a pooled connection is held"

    original_attachment = scan_api.scan_attachment_bytes

    async def _observe_attachment(data: bytes, **kwargs: str) -> ScanResult:
        checked_out.append(_checked_out_connections())
        return await original_attachment(data, **kwargs)

    monkeypatch.setattr(scan_api, "scan_attachment_bytes", _observe_attachment)
    uploaded = await api_client.post(
        "/api/v1/scan/attachment",
        data={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "attachment_id": uuid.uuid4().hex,
        },
        files={"file": ("rota.txt", b"Ward rota, nothing sensitive.", "text/plain")},
    )

    assert uploaded.status_code == 200, uploaded.text
    assert checked_out == [0, 0], "attachment detection must not hold a pooled connection either"


async def test_a_concurrent_duplicate_attachment_upload_returns_the_stored_row(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, org_code = await _signup(api_client)
    client_scan_id = uuid.uuid4().hex
    attachment_id = uuid.uuid4().hex
    org_id = await _org_id(db_session, org_code)
    payload = b"Ward rota for next week. Nothing sensitive in here at all."
    original = scan_api.scan_attachment_bytes
    winner: list[UUID] = []

    async def _insert_the_duplicate_then_scan(data: bytes, **kwargs: str) -> ScanResult:
        if not winner:
            row = AttachmentScan(
                org_id=org_id,
                client_scan_id=client_scan_id,
                attachment_id=attachment_id,
                filename="rota.txt",
                mime_type="text/plain",
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status="scanned",
                severity="none",
                risk_score=0,
                entities=[],
                extraction_errors=[],
            )
            db_session.add(row)
            await db_session.commit()
            winner.append(row.id)
        return await original(data, **kwargs)

    monkeypatch.setattr(scan_api, "scan_attachment_bytes", _insert_the_duplicate_then_scan)

    response = await api_client.post(
        "/api/v1/scan/attachment",
        data={
            "org_code": org_code,
            "client_scan_id": client_scan_id,
            "attachment_id": attachment_id,
        },
        files={"file": ("rota.txt", payload, "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["attachment_scan_id"] == str(winner[0])

    stored = (
        await db_session.scalars(
            select(AttachmentScan).where(
                AttachmentScan.org_id == org_id,
                AttachmentScan.client_scan_id == client_scan_id,
            )
        )
    ).all()
    assert len(stored) == 1


async def test_an_oversized_attachment_upload_is_refused(api_client: AsyncClient) -> None:
    _token, org_code = await _signup(api_client)
    from blade_backend.settings import get_settings  # noqa: PLC0415

    oversized = b"x" * (get_settings().scan_max_attachment_bytes + 128 * 1024)

    response = await api_client.post(
        "/api/v1/scan/attachment",
        data={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "attachment_id": uuid.uuid4().hex,
        },
        files={"file": ("huge.bin", oversized, "application/octet-stream")},
    )

    assert response.status_code == 413


async def test_an_attachment_that_fails_to_scan_does_not_leak_internal_errors(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, org_code = await _signup(api_client)

    async def _explode(data: bytes, **kwargs: str) -> ScanResult:
        del data, kwargs
        raise RuntimeError("/var/lib/blade/secret-path.pdf could not be parsed by libmagic")

    monkeypatch.setattr(scan_api, "scan_attachment_bytes", _explode)

    response = await api_client.post(
        "/api/v1/scan/attachment",
        data={
            "org_code": org_code,
            "client_scan_id": uuid.uuid4().hex,
            "attachment_id": uuid.uuid4().hex,
        },
        files={"file": ("rota.txt", b"Ward rota, nothing sensitive.", "text/plain")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "attachment could not be scanned"
    assert "secret-path" not in response.text
