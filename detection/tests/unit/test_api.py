from __future__ import annotations

import time
from pathlib import Path

from docx import Document

from blade_detection.api import MAX_ENTITIES_PER_TYPE, detect_email, top_entities_per_type
from blade_detection.models import Attachment, EmailPayload, Entity


def test_detect_email_finds_abha() -> None:
    result = detect_email(
        EmailPayload(
            subject="",
            body="ABHA 12-3456-7890-1234 for discharge summary",
            recipients=[],
        )
    )

    abha = [entity for entity in result.entities if entity.type == "ABHA_NUMBER"]
    assert len(abha) == 1
    assert abha[0].masked_value.endswith("1234")
    assert result.risk_score > 0


def test_detect_email_masks_raw_values() -> None:
    result = detect_email(EmailPayload(body="Aadhaar 7534 7930 7460 on file"))

    assert result.entities
    for entity in result.entities:
        assert "7534" not in entity.masked_value


def test_detect_email_scans_docx_attachment(tmp_path: Path) -> None:
    document_path = tmp_path / "report.docx"
    document = Document()
    document.add_paragraph("Patient UHID 0024518 has diagnosis E11.9")
    document.save(str(document_path))

    result = detect_email(
        EmailPayload(
            attachments=[
                Attachment(
                    id="attachment-1",
                    filename="report.docx",
                    mime_type=(
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    size_bytes=document_path.stat().st_size,
                    sha256="placeholder",
                    local_path=str(document_path),
                )
            ]
        )
    )

    assert {"MRN", "ICD10"} <= {entity.type for entity in result.entities}
    assert {entity.source for entity in result.entities} == {"attachment"}
    assert {entity.attachment_id for entity in result.entities} == {"attachment-1"}


def test_detect_email_text_path_perf_under_budget() -> None:
    body = " ".join(["routine"] * 500)
    body += " ABHA 12-3456-7890-1234 UHID 0024518 diagnosis E11.9"

    detect_email(EmailPayload(body=body))
    started = time.perf_counter()
    result = detect_email(EmailPayload(body=body))
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert {"ABHA_NUMBER", "MRN", "ICD10"} <= {entity.type for entity in result.entities}
    assert elapsed_ms < 500


def test_overlapping_matches_resolve_to_the_composite_identifier() -> None:
    result = detect_email(EmailPayload(body="ABHA 96-9015-1720-1488 verified"))

    types = [entity.type for entity in result.entities]
    assert "ABHA_NUMBER" in types
    assert "IN_AADHAAR" not in types


def test_invalid_icd10_codes_are_dropped() -> None:
    result = detect_email(
        EmailPayload(body="Clinical meeting in room A12, patient needs vitamin B12.")
    )

    assert [entity for entity in result.entities if entity.type == "ICD10"] == []


def test_stored_entities_are_capped_per_type_while_the_count_is_kept() -> None:
    line = "UHID {index:07d} admitted for treatment.\n"
    body = "".join(line.format(index=index) for index in range(MAX_ENTITIES_PER_TYPE + 40))

    result = detect_email(EmailPayload(body=body))

    stored = [entity for entity in result.entities if entity.type == "MRN"]
    assert len(stored) == MAX_ENTITIES_PER_TYPE
    assert result.entity_count >= MAX_ENTITIES_PER_TYPE + 40
    assert result.entity_count > len(result.entities)
    assert [entity.start for entity in stored] == sorted(entity.start or 0 for entity in stored)


def test_a_small_scan_keeps_every_entity_and_reports_the_same_count() -> None:
    result = detect_email(EmailPayload(body="UHID 0024518 and Aadhaar 7534 7930 7460."))

    assert result.entity_count == len(result.entities)
    assert result.entity_count > 0


def test_top_entities_per_type_keeps_the_highest_confidence_hits() -> None:
    entities = [
        Entity(type="MRN", masked_value="*", confidence=0.6, source="body", start=0, end=1),
        Entity(type="MRN", masked_value="*", confidence=0.9, source="body", start=2, end=3),
        Entity(type="MRN", masked_value="*", confidence=0.7, source="body", start=4, end=5),
        Entity(type="ICD10", masked_value="*", confidence=0.5, source="body", start=6, end=7),
    ]

    kept = top_entities_per_type(entities, 2)

    assert [entity.start for entity in kept] == [2, 4, 6]
