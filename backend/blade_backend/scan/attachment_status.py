from __future__ import annotations

from blade_backend.db.models import AttachmentScanStatus

FULLY_SCANNED: AttachmentScanStatus = "scanned"
PARTIALLY_SCANNED: AttachmentScanStatus = "partial"
NOT_SCANNED: AttachmentScanStatus = "failed"

CHECKED_STATUSES: frozenset[str] = frozenset({FULLY_SCANNED, PARTIALLY_SCANNED})


def status_for_extraction(
    *,
    extraction_errors: list[str],
    entity_count: int,
) -> AttachmentScanStatus:
    if not extraction_errors:
        return FULLY_SCANNED
    return PARTIALLY_SCANNED if entity_count else NOT_SCANNED


def contributes_entities(status: str) -> bool:
    return status in CHECKED_STATUSES


def is_fully_scanned(status: str) -> bool:
    return status == FULLY_SCANNED
