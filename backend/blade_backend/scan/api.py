from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import structlog
from blade_detection.models import Entity as DetectionEntity
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from blade_backend.audit.service import write_audit_event
from blade_backend.db.models import (
    ApprovedDomain,
    AttachmentScan,
    DomainClass,
    EventAction,
    EventSeverity,
    Organization,
    QuarantineItem,
    ScanEvent,
)
from blade_backend.deps import DbSession
from blade_backend.policy import (
    UNSCANNED_ATTACHMENT_RULE,
    PolicyDecision,
    PolicySet,
    SenderClass,
    build_facts,
    evaluate,
    load_policy_set,
)
from blade_backend.scan.attachment_status import (
    contributes_entities,
    is_fully_scanned,
    status_for_extraction,
)
from blade_backend.scan.credentials import ScanPrincipal, principal_for_request
from blade_backend.scan.limits import enforce_scan_limit
from blade_backend.scan.runner import (
    DetectionTimeoutError,
    DetectionUnavailableError,
    scan_attachment_bytes,
    scan_text,
)
from blade_backend.settings import get_settings
from blade_backend.storage import get_store
from blade_backend.tasks.scan_tasks import process_attachment_scan
from blade_backend.utils.uuid import uuid7

logger = structlog.get_logger(__name__)

router = APIRouter()

RecipientClass = Literal[
    "internal",
    "approved_partner",
    "blocked",
    "external",
    "public_email",
    "unknown",
]
AttachmentStatus = Literal["scanned", "partial", "queued", "failed"]

_DOMAIN_CLASS_RANK: dict[DomainClass, int] = {"internal": 0, "partner": 1, "blocked": 2}
_RECIPIENT_CLASS_FOR_DOMAIN: dict[DomainClass, RecipientClass] = {
    "internal": "internal",
    "partner": "approved_partner",
    "blocked": "blocked",
}
_SENDER_CLASS_FOR_DOMAIN: dict[DomainClass, SenderClass] = {
    "internal": "internal",
    "partner": "approved_partner",
    "blocked": "external",
}
UPLOAD_CHUNK_BYTES = 256 * 1024
TIMED_OUT_ATTACHMENT_ERROR = "attachment scan timed out"
UNREADABLE_ATTACHMENT_ERROR = "attachment could not be scanned"
UNSCANNED_SUBJECT_PLACEHOLDER = "[subject not scanned]"

_SEVERITY_RANK: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
}


class EntityHit(BaseModel):
    type: str
    masked_value: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["body", "subject", "attachment"]
    attachment_id: str | None = None


class RecipientHit(BaseModel):
    email: str
    classification: RecipientClass


class Verdict(BaseModel):
    scan_id: str
    action: EventAction
    severity: EventSeverity
    risk_score: float
    matched_policy_ids: list[str]
    entities: list[EntityHit]
    recipients: list[RecipientHit]
    user_message: str
    created_at: str
    quarantine_id: str | None = None
    degraded: bool = False


class ScanEmailRequest(BaseModel):
    org_code: str | None = Field(default=None, min_length=3, max_length=128)
    client_scan_id: str = Field(min_length=4, max_length=128)
    subject: str = Field(default="", max_length=5000)
    body: str = Field(default="", max_length=1_000_000)
    recipients: list[EmailStr] = Field(default_factory=list, max_length=200)
    user_email: EmailStr | None = None

    @field_validator("org_code")
    @classmethod
    def normalize_org_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @field_validator("client_scan_id")
    @classmethod
    def normalize_client_scan_id(cls, value: str) -> str:
        return value.strip()


def _empty_attachment_scan_ids() -> list[UUID]:
    return []


class ScanFinalizeRequest(ScanEmailRequest):
    attachment_scan_ids: list[UUID] = Field(
        default_factory=_empty_attachment_scan_ids,
        max_length=50,
    )


class AttachmentScanResponse(BaseModel):
    attachment_scan_id: str
    status: AttachmentStatus
    verdict: Verdict | None = None
    error: str | None = None


def _empty_entity_payloads() -> list[dict[str, object]]:
    return []


@dataclass(frozen=True, slots=True)
class AttachmentFinding:
    id: UUID
    attachment_id: str
    filename: str
    sha256: str
    status: AttachmentStatus
    severity: EventSeverity
    risk_score: float
    entities: list[dict[str, object]] = field(default_factory=_empty_entity_payloads)


def _finding(row: AttachmentScan) -> AttachmentFinding:
    return AttachmentFinding(
        id=row.id,
        attachment_id=row.attachment_id,
        filename=row.filename,
        sha256=row.sha256,
        status=row.status,
        severity=row.severity,
        risk_score=float(row.risk_score),
        entities=list(row.entities or []),
    )


def _empty_recipient_hits() -> list[RecipientHit]:
    return []


@dataclass(frozen=True, slots=True)
class ScanContext:
    org_id: UUID
    policy_set: PolicySet
    sender_class: SenderClass
    recipient_hits: list[RecipientHit] = field(default_factory=_empty_recipient_hits)


def _empty_entity_hits() -> list[EntityHit]:
    return []


@dataclass(frozen=True, slots=True)
class MessageDetection:
    severity: EventSeverity
    risk_score: float
    unread_parts: int
    masked_subject: str = ""
    entities: list[EntityHit] = field(default_factory=_empty_entity_hits)


def _sender_email(principal: ScanPrincipal, claimed: object) -> str | None:
    if principal.email:
        return principal.email.lower()
    if claimed:
        return str(claimed).lower()
    return None


async def _org_id_for(session: AsyncSession, principal: ScanPrincipal) -> UUID:
    org_id = await session.scalar(
        select(Organization.id).where(Organization.id == principal.org_id)
    )
    if org_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown organization")
    return org_id


def _domain(email: str) -> str:
    value = email.strip().lower()
    if "<" in value and ">" in value:
        value = value.replace("<", " ").replace(">", " ").split()[-1]
    at = value.rfind("@")
    return value[at + 1 :] if at >= 0 else ""


def _addr(email: str) -> str:
    value = email.strip().lower()
    if "<" in value and ">" in value:
        value = value.replace("<", " ").replace(">", " ").split()[-1]
    return value


def _domain_matches(candidate: str, configured: str) -> bool:
    return candidate == configured or candidate.endswith(f".{configured}")


def _match_specificity(email: str, email_domain: str, configured: str) -> int | None:
    if "@" in configured:
        return len(configured) + 1 if email == configured else None
    if email_domain and _domain_matches(email_domain, configured):
        return len(configured)
    return None


def _most_specific_domain(
    email: str,
    email_domain: str,
    domains: Sequence[ApprovedDomain],
) -> DomainClass | None:
    best: DomainClass | None = None
    best_specificity = -1
    best_rank = -1
    for configured in domains:
        specificity = _match_specificity(email, email_domain, configured.domain.lower())
        if specificity is None:
            continue
        rank = _DOMAIN_CLASS_RANK[configured.classification]
        if specificity < best_specificity:
            continue
        if specificity == best_specificity and rank <= best_rank:
            continue
        best, best_specificity, best_rank = configured.classification, specificity, rank
    return best


async def _classify_recipients(
    session: AsyncSession,
    org_id: UUID,
    recipients: Iterable[str],
) -> list[RecipientHit]:
    normalized = [_addr(str(recipient)) for recipient in recipients if _addr(str(recipient))]
    domains = (
        await session.scalars(
            select(ApprovedDomain).where(
                ApprovedDomain.org_id == org_id,
                ApprovedDomain.direction.in_(("recipient", "both")),
            )
        )
    ).all()
    hits: list[RecipientHit] = []
    for email in normalized:
        recipient_domain = _domain(email)
        classification: RecipientClass = "unknown" if not recipient_domain else "external"
        if recipient_domain in _PUBLIC_EMAIL_DOMAINS:
            classification = "public_email"
        matched = _most_specific_domain(email, recipient_domain, domains)
        if matched is not None:
            classification = _RECIPIENT_CLASS_FOR_DOMAIN[matched]
        hits.append(RecipientHit(email=email, classification=classification))
    return hits


def _entity_hit(raw: DetectionEntity | dict[str, object]) -> EntityHit:
    data = cast(dict[str, object], raw.model_dump()) if isinstance(raw, DetectionEntity) else raw
    source_raw = data.get("source")
    if source_raw not in {"body", "subject", "attachment"}:
        raise ValueError("unsupported entity source")
    source = cast(Literal["body", "subject", "attachment"], source_raw)
    confidence_raw = data.get("confidence", 0)
    confidence = confidence_raw if isinstance(confidence_raw, int | float | str) else 0
    attachment_id = data.get("attachment_id")
    return EntityHit(
        type=str(data.get("type", "")),
        masked_value=str(data.get("masked_value", "")),
        confidence=float(confidence),
        source=source,
        attachment_id=str(attachment_id) if attachment_id else None,
    )


def _max_severity(values: Iterable[str]) -> EventSeverity:
    severity = "none"
    for value in values:
        if _SEVERITY_RANK.get(value, 0) > _SEVERITY_RANK[severity]:
            severity = value
    return severity  # type: ignore[return-value]


async def _classify_sender(
    session: AsyncSession,
    org_id: UUID,
    sender: str,
) -> SenderClass:
    address = _addr(sender)
    if not address:
        return "unknown"
    sender_domain = _domain(address)
    if not sender_domain:
        return "unknown"

    domains = (
        await session.scalars(
            select(ApprovedDomain).where(
                ApprovedDomain.org_id == org_id,
                ApprovedDomain.direction.in_(("sender", "both")),
            )
        )
    ).all()

    matched = _most_specific_domain(address, sender_domain, domains)
    if matched is not None:
        return _SENDER_CLASS_FOR_DOMAIN[matched]
    return "public_email" if sender_domain in _PUBLIC_EMAIL_DOMAINS else "external"


def _policy_decision(
    *,
    entities: list[EntityHit],
    recipients: list[RecipientHit],
    detected_severity: EventSeverity,
    detected_risk_score: float,
    sender_class: SenderClass = "internal",
    has_attachments: bool = False,
    attachments_unscanned: int = 0,
    policy_set: PolicySet | None = None,
) -> PolicyDecision:
    facts = build_facts(
        entities=[(entity.type, entity.masked_value) for entity in entities],
        risk_score=detected_risk_score,
        severity=detected_severity,
        recipient_classes=[recipient.classification for recipient in recipients],
        sender_class=sender_class,
        has_attachments=has_attachments,
        attachments_unscanned=attachments_unscanned,
    )
    decision = evaluate(facts, policy_set)
    return PolicyDecision(
        action=decision.action,
        severity=decision.severity,
        risk_score=round(decision.risk_score, 2),
        matched_policy_ids=decision.matched_policy_ids,
        user_message=decision.user_message,
    )


def _build_verdict(
    *,
    entities: list[EntityHit],
    recipients: list[RecipientHit],
    detected_severity: EventSeverity,
    detected_risk_score: float,
    sender_class: SenderClass = "internal",
    has_attachments: bool = False,
    attachments_unscanned: int = 0,
    policy_set: PolicySet | None = None,
    quarantine_id: UUID | None = None,
) -> Verdict:
    decision = _policy_decision(
        entities=entities,
        recipients=recipients,
        detected_severity=detected_severity,
        detected_risk_score=detected_risk_score,
        sender_class=sender_class,
        has_attachments=has_attachments,
        attachments_unscanned=attachments_unscanned,
        policy_set=policy_set,
    )
    return Verdict(
        scan_id=str(uuid7()),
        action=decision.action,
        severity=decision.severity,
        risk_score=round(decision.risk_score, 2),
        matched_policy_ids=decision.matched_policy_ids,
        entities=entities,
        recipients=recipients,
        user_message=decision.user_message,
        created_at=datetime.now(UTC).isoformat(),
        quarantine_id=str(quarantine_id) if quarantine_id else None,
        degraded=attachments_unscanned > 0,
    )


async def _record_scan_event(
    session: AsyncSession,
    *,
    org_id: UUID,
    client_event_id: str,
    verdict: Verdict,
    user_email: str | None,
) -> ScanEvent:
    existing = await session.scalar(
        select(ScanEvent).where(
            ScanEvent.org_id == org_id,
            ScanEvent.client_event_id == client_event_id,
        )
    )
    if existing is not None:
        return existing

    event = ScanEvent(
        client_event_id=client_event_id,
        org_id=org_id,
        user_email=user_email,
        action=verdict.action,
        severity=verdict.severity,
        risk_score=verdict.risk_score,
        entities=[entity.model_dump(exclude_none=True) for entity in verdict.entities],
        recipients=[recipient.email for recipient in verdict.recipients],
        event_time=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event


async def _record_quarantine(
    session: AsyncSession,
    *,
    org_id: UUID,
    scan_event_id: UUID | None,
    client_scan_id: str,
    sender: str,
    subject: str,
    verdict: Verdict,
    findings: list[AttachmentFinding],
) -> QuarantineItem:
    item = QuarantineItem(
        org_id=org_id,
        scan_event_id=scan_event_id,
        scan_id=verdict.scan_id,
        client_scan_id=client_scan_id,
        sender=sender,
        subject=subject[:500],
        recipients=[recipient.email for recipient in verdict.recipients],
        entities=[entity.model_dump(exclude_none=True) for entity in verdict.entities],
        matched_policy_ids=verdict.matched_policy_ids,
        risk_score=verdict.risk_score,
        severity=verdict.severity,
        status="pending",
        attachment_refs=[
            {
                "attachment_scan_id": str(finding.id),
                "attachment_id": finding.attachment_id,
                "filename": finding.filename,
                "sha256": finding.sha256,
                "status": finding.status,
            }
            for finding in findings
        ],
    )
    session.add(item)
    await session.flush()
    return item


async def _load_scan_context(
    session: AsyncSession,
    *,
    org_id: UUID,
    recipients: list[str],
    user_email: str | None,
) -> ScanContext:
    recipient_hits = await _classify_recipients(session, org_id, recipients)
    sender_class = await _classify_sender(session, org_id, user_email) if user_email else "unknown"
    policy_set = await load_policy_set(session, org_id)
    return ScanContext(
        org_id=org_id,
        policy_set=policy_set,
        sender_class=sender_class,
        recipient_hits=recipient_hits,
    )


async def _detect_message(subject: str, body: str, recipients: list[str]) -> MessageDetection:
    try:
        result = await scan_text(subject, body, recipients)
    except (DetectionTimeoutError, DetectionUnavailableError) as exc:
        logger.warning("message detection did not complete", reason=type(exc).__name__)
        return MessageDetection(
            severity="none",
            risk_score=0,
            unread_parts=1,
            masked_subject=UNSCANNED_SUBJECT_PLACEHOLDER,
            entities=[],
        )
    return MessageDetection(
        severity=result.severity,
        risk_score=float(result.risk_score),
        unread_parts=0,
        masked_subject=_mask_subject(subject, result.entities),
        entities=[_entity_hit(entity) for entity in result.entities],
    )


def _mask_subject(subject: str, entities: list[DetectionEntity]) -> str:
    spans = sorted(
        (entity.start, entity.end, entity.masked_value)
        for entity in entities
        if entity.source == "subject" and entity.start is not None and entity.end is not None
    )
    masked: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        if start < cursor or start >= end or end > len(subject):
            continue
        masked.append(subject[cursor:start])
        masked.append(replacement)
        cursor = end
    masked.append(subject[cursor:])
    return "".join(masked)


async def _record_verdict(
    session: AsyncSession,
    *,
    context: ScanContext,
    detection: MessageDetection,
    findings: list[AttachmentFinding],
    client_scan_id: str,
    user_email: str | None,
    audit_client_event_id: str,
    principal_actor: str,
) -> Verdict:
    entities = [
        *detection.entities,
        *(
            _entity_hit(entity)
            for finding in findings
            if contributes_entities(finding.status)
            for entity in finding.entities
        ),
    ]
    unread_parts = detection.unread_parts + sum(
        1 for finding in findings if not is_fully_scanned(finding.status)
    )
    detected_risk = max(
        [detection.risk_score, *[finding.risk_score for finding in findings]],
        default=0,
    )
    detected_severity = _max_severity(
        [detection.severity, *[finding.severity for finding in findings]]
    )
    verdict = _build_verdict(
        entities=entities,
        recipients=context.recipient_hits,
        detected_severity=detected_severity,
        detected_risk_score=detected_risk,
        sender_class=context.sender_class,
        has_attachments=bool(findings),
        attachments_unscanned=unread_parts,
        policy_set=context.policy_set,
    )
    event = await _record_scan_event(
        session,
        org_id=context.org_id,
        client_event_id=audit_client_event_id,
        verdict=verdict,
        user_email=user_email,
    )
    if verdict.action == "quarantine":
        quarantine = await _record_quarantine(
            session,
            org_id=context.org_id,
            scan_event_id=event.id,
            client_scan_id=client_scan_id,
            sender=user_email or "unattributed",
            subject=detection.masked_subject,
            verdict=verdict,
            findings=findings,
        )
        verdict.quarantine_id = str(quarantine.id)
        await write_audit_event(
            session,
            org_id=context.org_id,
            actor=principal_actor,
            category="quarantine",
            action="created",
            metadata={
                "quarantine_id": str(quarantine.id),
                "scan_id": verdict.scan_id,
                "client_scan_id": client_scan_id,
                "risk_score": verdict.risk_score,
            },
        )

    await write_audit_event(
        session,
        org_id=context.org_id,
        actor=principal_actor,
        category="scan",
        action=verdict.action,
        metadata={
            "scan_id": verdict.scan_id,
            "client_scan_id": client_scan_id,
            "matched_policy_ids": verdict.matched_policy_ids,
            "entity_count": len(verdict.entities),
            "recipient_count": len(verdict.recipients),
            "sender_class": context.sender_class,
            "risk_score": verdict.risk_score,
            "severity": verdict.severity,
            "attachments_unscanned": unread_parts,
            "message_scan_incomplete": bool(detection.unread_parts),
            "degraded": verdict.degraded,
        },
    )
    return verdict


async def _scan_message(
    session: AsyncSession,
    *,
    principal: ScanPrincipal,
    org_id: UUID,
    client_scan_id: str,
    subject: str,
    body: str,
    recipients: list[str],
    user_email: str | None,
    findings: list[AttachmentFinding],
    audit_client_event_id: str,
    claimed_email: str | None,
) -> Verdict:
    context = await _load_scan_context(
        session,
        org_id=org_id,
        recipients=recipients,
        user_email=user_email,
    )
    await session.close()

    detection = await _detect_message(subject, body, recipients)

    verdict = await _record_verdict(
        session,
        context=context,
        detection=detection,
        findings=findings,
        client_scan_id=client_scan_id,
        user_email=user_email,
        audit_client_event_id=audit_client_event_id,
        principal_actor=principal.actor(claimed_email),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="scan id already exists") from exc
    return verdict


def _is_deep_scan_candidate(filename: str, mime_type: str, size_bytes: int) -> bool:
    settings = get_settings()
    lower_name = filename.lower()
    lower_mime = mime_type.lower()
    return (
        size_bytes > settings.scan_deep_scan_threshold_bytes
        or lower_mime.startswith("image/")
        or lower_name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"))
    )


async def _store_queued_attachment(data: bytes, filename: str) -> str:
    store = get_store()
    suffix = filename[filename.rfind(".") :] if "." in filename else ""
    return await asyncio.to_thread(store.put, data, suffix=suffix)


def _attachment_verdict(row: AttachmentScan) -> Verdict | None:
    if not contributes_entities(row.status):
        return None
    partial = not is_fully_scanned(row.status)
    return Verdict(
        scan_id=str(row.id),
        action="warn" if partial else "allow",
        severity=row.severity,
        risk_score=float(row.risk_score),
        matched_policy_ids=[UNSCANNED_ATTACHMENT_RULE.id] if partial else [],
        entities=[_entity_hit(entity) for entity in row.entities],
        recipients=[],
        user_message=(UNSCANNED_ATTACHMENT_RULE.user_message if partial else "Attachment scanned."),
        created_at=row.created_at.isoformat(),
        degraded=partial,
    )


@router.post("/email", response_model=Verdict)
async def scan_email(
    payload: ScanEmailRequest,
    session: DbSession,
    x_blade_device_token: Annotated[str | None, Header()] = None,
) -> Verdict:
    """Scan a message body and record the verdict, releasing the database connection first.

    A quarantined message keeps its sender and recipient addresses in clear,
    because a reviewer decides on the strength of who was about to receive
    what. The subject is stored with every identifier the scan found in it
    replaced by its mask, so the queue does not become a second copy of the
    data it exists to hold back. Bodies and attachment contents are never
    stored here at all.
    """
    principal = await principal_for_request(session, payload.org_code, x_blade_device_token)
    enforce_scan_limit(principal)
    org_id = await _org_id_for(session, principal)
    return await _scan_message(
        session,
        principal=principal,
        org_id=org_id,
        client_scan_id=payload.client_scan_id,
        subject=payload.subject,
        body=payload.body,
        recipients=[str(recipient).lower() for recipient in payload.recipients],
        user_email=_sender_email(principal, payload.user_email),
        findings=[],
        audit_client_event_id=f"{payload.client_scan_id}:email",
        claimed_email=payload.user_email,
    )


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().scan_max_attachment_bytes
    buffer = bytearray()
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            return bytes(buffer)
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="attachment is larger than the configured limit",
            )


async def _existing_attachment_scan(
    session: AsyncSession,
    *,
    org_id: UUID,
    client_scan_id: str,
    attachment_id: str,
) -> AttachmentScan | None:
    return await session.scalar(
        select(AttachmentScan).where(
            AttachmentScan.org_id == org_id,
            AttachmentScan.client_scan_id == client_scan_id,
            AttachmentScan.attachment_id == attachment_id,
        )
    )


async def _resolve_duplicate_upload(
    session: AsyncSession,
    *,
    org_id: UUID,
    client_scan_id: str,
    attachment_id: str,
) -> AttachmentScanResponse:
    await session.rollback()
    duplicate = await _existing_attachment_scan(
        session,
        org_id=org_id,
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
    )
    if duplicate is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="attachment scan could not be stored",
        )
    logger.info(
        "concurrent duplicate attachment upload resolved to the stored scan",
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
    )
    return _attachment_response(duplicate)


def _attachment_response(row: AttachmentScan) -> AttachmentScanResponse:
    return AttachmentScanResponse(
        attachment_scan_id=str(row.id),
        status=row.status,
        verdict=_attachment_verdict(row),
        error=row.error,
    )


@dataclass(frozen=True, slots=True)
class _AttachmentOutcome:
    status: AttachmentStatus
    severity: EventSeverity
    risk_score: float
    entities: list[dict[str, object]]
    extraction_errors: list[str]
    error: str | None


async def _scan_attachment_outcome(
    data: bytes,
    *,
    attachment_id: str,
    filename: str,
    mime_type: str,
    sha256: str,
) -> _AttachmentOutcome:
    try:
        result = await scan_attachment_bytes(
            data,
            attachment_id=attachment_id,
            filename=filename,
            mime_type=mime_type,
            sha256=sha256,
        )
    except DetectionTimeoutError:
        logger.warning("attachment detection exceeded its deadline", sha256=sha256)
        return _AttachmentOutcome(
            status="failed",
            severity="none",
            risk_score=0,
            entities=[],
            extraction_errors=[],
            error=TIMED_OUT_ATTACHMENT_ERROR,
        )
    except Exception as exc:
        logger.warning(
            "attachment detection failed",
            sha256=sha256,
            reason=type(exc).__name__,
            detail=str(exc)[:500],
        )
        return _AttachmentOutcome(
            status="failed",
            severity="none",
            risk_score=0,
            entities=[],
            extraction_errors=[],
            error=UNREADABLE_ATTACHMENT_ERROR,
        )

    entities = [_entity_hit(entity).model_dump(exclude_none=True) for entity in result.entities]
    return _AttachmentOutcome(
        status=status_for_extraction(
            extraction_errors=result.extraction_errors,
            entity_count=len(entities),
        ),
        severity=result.severity,
        risk_score=float(result.risk_score),
        entities=entities,
        extraction_errors=result.extraction_errors,
        error="; ".join(result.extraction_errors[:3]) if result.extraction_errors else None,
    )


async def _persist_attachment_scan(
    session: AsyncSession,
    *,
    principal: ScanPrincipal,
    org_id: UUID,
    client_scan_id: str,
    attachment_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    sha256: str,
    outcome: _AttachmentOutcome,
) -> AttachmentScanResponse:
    row = AttachmentScan(
        org_id=org_id,
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        status=outcome.status,
        severity=outcome.severity,
        risk_score=outcome.risk_score,
        entities=outcome.entities,
        extraction_errors=outcome.extraction_errors,
        error=outcome.error,
    )
    try:
        session.add(row)
        await write_audit_event(
            session,
            org_id=org_id,
            actor=principal.actor(),
            category="scan",
            action=f"attachment_{outcome.status}",
            metadata={
                "client_scan_id": client_scan_id,
                "attachment_id": attachment_id,
                "filename": filename,
                "sha256": sha256,
                "entity_count": len(outcome.entities),
            },
        )
        await session.commit()
    except IntegrityError:
        return await _resolve_duplicate_upload(
            session,
            org_id=org_id,
            client_scan_id=client_scan_id,
            attachment_id=attachment_id,
        )
    await session.refresh(row)
    return _attachment_response(row)


async def _queue_attachment_scan(
    session: AsyncSession,
    *,
    principal: ScanPrincipal,
    org_id: UUID,
    client_scan_id: str,
    attachment_id: str,
    filename: str,
    mime_type: str,
    sha256: str,
    data: bytes,
) -> AttachmentScanResponse:
    await session.close()
    storage_key = await _store_queued_attachment(data, filename)
    row = AttachmentScan(
        org_id=org_id,
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256,
        status="queued",
        severity="none",
        risk_score=0,
        entities=[],
        extraction_errors=[],
        storage_path=storage_key,
    )
    try:
        session.add(row)
        await write_audit_event(
            session,
            org_id=org_id,
            actor=principal.actor(),
            category="scan",
            action="attachment_queued",
            metadata={
                "client_scan_id": client_scan_id,
                "attachment_id": attachment_id,
                "filename": filename,
                "sha256": sha256,
            },
        )
        await session.commit()
    except IntegrityError:
        await asyncio.to_thread(get_store().delete, storage_key)
        return await _resolve_duplicate_upload(
            session,
            org_id=org_id,
            client_scan_id=client_scan_id,
            attachment_id=attachment_id,
        )
    await session.refresh(row)
    with suppress(Exception):
        cast(Any, process_attachment_scan).delay(str(row.id))
    return AttachmentScanResponse(attachment_scan_id=str(row.id), status="queued")


@router.post("/attachment", response_model=AttachmentScanResponse)
async def scan_attachment(
    session: DbSession,
    client_scan_id: Annotated[str, Form(..., min_length=4, max_length=128)],
    attachment_id: Annotated[str, Form(..., min_length=1, max_length=128)],
    file: Annotated[UploadFile, File(...)],
    org_code: Annotated[str | None, Form(min_length=3, max_length=128)] = None,
    x_blade_device_token: Annotated[str | None, Header()] = None,
) -> AttachmentScanResponse:
    """Scan one attachment, or queue it when it needs a deep scan."""
    principal = await principal_for_request(session, org_code, x_blade_device_token)
    enforce_scan_limit(principal)
    org_id = await _org_id_for(session, principal)
    data = await _read_upload(file)
    filename = file.filename or attachment_id
    mime_type = file.content_type or "application/octet-stream"
    sha256 = hashlib.sha256(data).hexdigest()

    existing = await _existing_attachment_scan(
        session,
        org_id=org_id,
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
    )
    if existing is not None:
        return _attachment_response(existing)

    if _is_deep_scan_candidate(filename, mime_type, len(data)):
        return await _queue_attachment_scan(
            session,
            principal=principal,
            org_id=org_id,
            client_scan_id=client_scan_id,
            attachment_id=attachment_id,
            filename=filename,
            mime_type=mime_type,
            sha256=sha256,
            data=data,
        )

    await session.close()
    outcome = await _scan_attachment_outcome(
        data,
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        sha256=sha256,
    )
    return await _persist_attachment_scan(
        session,
        principal=principal,
        org_id=org_id,
        client_scan_id=client_scan_id,
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256,
        outcome=outcome,
    )


@router.get("/attachment/{attachment_scan_id}", response_model=AttachmentScanResponse)
async def get_attachment_scan(
    attachment_scan_id: UUID,
    session: DbSession,
    org_code: Annotated[str | None, Query(min_length=3, max_length=128)] = None,
    x_blade_device_token: Annotated[str | None, Header()] = None,
) -> AttachmentScanResponse:
    principal = await principal_for_request(session, org_code, x_blade_device_token)
    enforce_scan_limit(principal)
    row = await session.get(AttachmentScan, attachment_scan_id)
    if row is None or row.org_id != principal.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="attachment scan not found")
    return AttachmentScanResponse(
        attachment_scan_id=str(row.id),
        status=row.status,
        verdict=_attachment_verdict(row),
        error=row.error,
    )


@router.post("/finalize", response_model=Verdict)
async def finalize_scan(
    payload: ScanFinalizeRequest,
    session: DbSession,
    x_blade_device_token: Annotated[str | None, Header()] = None,
) -> Verdict:
    """Combine attachment scans with a body scan and record the final verdict.

    Quarantine rows are written with the same masking as `POST /scan/email`:
    sender and recipients in clear for the reviewer, subject masked over every
    identifier the scan found in it.
    """
    principal = await principal_for_request(session, payload.org_code, x_blade_device_token)
    enforce_scan_limit(principal)
    org_id = await _org_id_for(session, principal)
    findings: list[AttachmentFinding] = []
    if payload.attachment_scan_ids:
        fetched = (
            await session.scalars(
                select(AttachmentScan).where(
                    AttachmentScan.org_id == org_id,
                    AttachmentScan.id.in_(payload.attachment_scan_ids),
                )
            )
        ).all()
        findings = [_finding(row) for row in fetched]
        found_ids = {finding.id for finding in findings}
        missing = [scan_id for scan_id in payload.attachment_scan_ids if scan_id not in found_ids]
        if missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="attachment scan not found")

    return await _scan_message(
        session,
        principal=principal,
        org_id=org_id,
        client_scan_id=payload.client_scan_id,
        subject=payload.subject,
        body=payload.body,
        recipients=[str(recipient).lower() for recipient in payload.recipients],
        user_email=_sender_email(principal, payload.user_email),
        findings=findings,
        audit_client_event_id=payload.client_scan_id,
        claimed_email=payload.user_email,
    )
