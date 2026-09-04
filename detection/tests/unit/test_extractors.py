from __future__ import annotations

import io
import struct
import zipfile
import zlib
from email.message import EmailMessage
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image

from blade_detection.api import detect_email
from blade_detection.extractors import extract_attachment, extract_bytes
from blade_detection.extractors.budget import MAX_EXTRACTION_CHARS, ExtractionBudget
from blade_detection.extractors.html import to_text
from blade_detection.extractors.issues import ExtractionIssue
from blade_detection.extractors.pdf import MAX_PDF_PAGES, MAX_RENDER_PIXELS, extract_pages
from blade_detection.models import Attachment, EmailPayload

PHI_LINE = "Patient Ramesh Kumar Iyer, UHID 0024518, diagnosis E11.9"


def _fitz() -> Any:
    return cast(Any, import_module("fitz"))


def _scan(path: Path, mime: str) -> set[str]:
    result = detect_email(
        EmailPayload(
            attachments=[
                Attachment(
                    id="a1",
                    filename=path.name,
                    mime_type=mime,
                    size_bytes=path.stat().st_size,
                    sha256="x",
                    local_path=str(path),
                )
            ]
        )
    )
    return {entity.type for entity in result.entities}


def test_csv_attachment_is_scanned(tmp_path: Path) -> None:
    path = tmp_path / "patients.csv"
    path.write_text("uhid,name,diagnosis\n0024518,Ramesh Kumar Iyer,E11.9\n", encoding="utf-8")
    assert "MRN" in _scan(path, "text/csv")


def test_plain_text_attachment_is_scanned(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text(PHI_LINE, encoding="utf-8")
    assert {"MRN", "PERSON"} <= _scan(path, "text/plain")


def test_docx_header_and_textbox_text_is_extracted() -> None:
    document = Document()
    document.add_paragraph("Body text with nothing sensitive.")
    document.sections[0].header.paragraphs[0].text = PHI_LINE
    buffer = io.BytesIO()
    document.save(buffer)

    result = extract_bytes(
        buffer.getvalue(),
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "a1",
    )
    assert "0024518" in result.text


def test_xlsx_rows_keep_header_context_on_one_line() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["UHID", "Name", "Code"])
    sheet.append(["0024518", "Ramesh Kumar Iyer", "E11.9"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = extract_bytes(
        buffer.getvalue(),
        "list.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "a1",
    )
    assert "UHID Name Code" in result.text
    assert "0024518" in result.text


def test_eml_attachment_extracts_headers_body_and_nested_files() -> None:
    inner = EmailMessage()
    inner["Subject"] = "Discharge summary"
    inner["From"] = "ward@hospital.in"
    inner.set_content("Please review the attached list.")
    inner.add_attachment(
        b"uhid,name\n0031902,Sunita Bhatt\n",
        maintype="text",
        subtype="csv",
        filename="list.csv",
    )

    result = extract_bytes(bytes(inner), "forwarded.eml", "message/rfc822", "a1")
    assert "Discharge summary" in result.text
    assert "0031902" in result.text


def test_zip_archive_members_are_scanned() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", PHI_LINE)
    result = extract_bytes(buffer.getvalue(), "bundle.zip", "application/zip", "a1")
    assert "0024518" in result.text


def test_archive_depth_is_capped() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("notes.txt", PHI_LINE)
    middle = io.BytesIO()
    with zipfile.ZipFile(middle, "w") as archive:
        archive.writestr("inner.zip", inner.getvalue())
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("middle.zip", middle.getvalue())

    result = extract_bytes(outer.getvalue(), "outer.zip", "application/zip", "a1")
    assert any("depth limit" in error for error in result.errors)


def _text_attachment(directory: Path, index: int, chars: int) -> Attachment:
    path = directory / f"note{index}.txt"
    path.write_text("A" * chars, encoding="utf-8")
    return Attachment(
        id=f"a{index}",
        filename=path.name,
        mime_type="text/plain",
        size_bytes=path.stat().st_size,
        sha256="x",
        local_path=str(path),
    )


def test_attachments_on_one_email_share_the_character_budget(tmp_path: Path) -> None:
    attachments = [_text_attachment(tmp_path, index, 600_000) for index in range(1, 4)]
    budget = ExtractionBudget()

    extracted = [extract_attachment(attachment, budget=budget) for attachment in attachments]

    assert sum(len(result.text) for result in extracted) <= MAX_EXTRACTION_CHARS
    assert any(ExtractionIssue.EXTRACTION_CHAR_LIMIT in error for error in extracted[1].errors)
    assert extracted[2].text == ""
    assert extracted[2].errors


def test_an_attachment_skipped_for_budget_is_reported_against_its_own_id(tmp_path: Path) -> None:
    attachments = [_text_attachment(tmp_path, index, 600_000) for index in range(1, 4)]

    result = detect_email(EmailPayload(attachments=attachments))

    assert any(
        error.startswith("a2: ") and ExtractionIssue.EXTRACTION_CHAR_LIMIT in error
        for error in result.extraction_errors
    )
    assert any(
        error.startswith("a3: ") and ExtractionIssue.EXTRACTION_BUDGET_EXHAUSTED in error
        for error in result.extraction_errors
    )


def test_nested_archives_share_one_character_budget() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(4):
            archive.writestr(f"big{index}.txt", "A" * 400_000)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(4):
            archive.writestr(f"inner{index}.zip", inner.getvalue())

    result = extract_bytes(outer.getvalue(), "outer.zip", "application/zip", "a1")

    assert len(result.text) <= MAX_EXTRACTION_CHARS
    assert any(ExtractionIssue.EXTRACTION_CHAR_LIMIT in error for error in result.errors)


def test_nested_emails_share_one_character_budget() -> None:
    inner = EmailMessage()
    inner["Subject"] = "Nested discharge summary"
    inner.set_content("A" * 600_000)

    outer = EmailMessage()
    outer["Subject"] = "Forwarded discharge summary"
    outer.set_content("B" * 600_000)
    outer.add_attachment(
        bytes(inner),
        maintype="application",
        subtype="octet-stream",
        filename="inner.eml",
    )

    result = extract_bytes(bytes(outer), "forwarded.eml", "message/rfc822", "a1")

    assert len(result.text) <= MAX_EXTRACTION_CHARS
    assert any(ExtractionIssue.EXTRACTION_CHAR_LIMIT in error for error in result.errors)


def test_archive_member_budget_spans_nested_archives() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(40):
            archive.writestr(f"note{index}.txt", PHI_LINE)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(4):
            archive.writestr(f"inner{index}.zip", inner.getvalue())

    result = extract_bytes(outer.getvalue(), "outer.zip", "application/zip", "a1")

    assert any(ExtractionIssue.ARCHIVE_MEMBER_LIMIT in error for error in result.errors)
    assert result.text.count("0024518") < 160


def test_oversized_pdf_page_is_clamped_and_reports_reduced_resolution() -> None:
    fitz = _fitz()
    document = fitz.open()
    document.new_page(width=45 * 72, height=45 * 72)
    data = document.tobytes()

    content = extract_pages(data)

    assert any(ExtractionIssue.PDF_RESOLUTION_REDUCED in issue for issue in content.issues)
    render = content.pages[0].render_ocr_image
    assert render is not None
    rendered = render()
    assert rendered is not None
    width, height = rendered.size
    assert width * height <= MAX_RENDER_PIXELS


def test_a4_pdf_page_is_rendered_at_the_target_resolution() -> None:
    fitz = _fitz()
    document = fitz.open()
    document.new_page(width=595, height=842)
    data = document.tobytes()

    content = extract_pages(data)

    assert ExtractionIssue.PDF_RESOLUTION_REDUCED not in content.issues


def test_renamed_file_is_still_classified_by_content() -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["UHID", "0024518"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = extract_bytes(buffer.getvalue(), "payload.dat", "application/octet-stream", "a1")
    assert "0024518" in result.text


def test_unsupported_type_reports_an_error_rather_than_silently_passing() -> None:
    result = extract_bytes(b"\x00\x01\x02binary", "thing.bin", "application/octet-stream", "a1")
    assert result.text == ""
    assert result.errors


@pytest.mark.parametrize("extension", [".png", ".jpg"])
def test_images_are_queued_for_ocr(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"scan{extension}"
    Image.new("RGB", (60, 40), "white").save(path)
    result = extract_bytes(path.read_bytes(), path.name, "application/octet-stream", "a1")
    assert result.ocr_sources


def test_ocr_unavailability_is_reported_not_silently_empty() -> None:
    from PIL import Image

    from blade_detection.config import DetectionConfig
    from blade_detection.ocr import extract_text, tesseract_backend

    def _unavailable(*_args: object, **_kwargs: object) -> tuple[str, float]:
        raise tesseract_backend.OcrUnavailableError("pytesseract is not installed")

    original = tesseract_backend.run
    tesseract_backend.run = _unavailable  # type: ignore[assignment]
    try:
        result = extract_text([Image.new("RGB", (40, 20), "white")], DetectionConfig())
    finally:
        tesseract_backend.run = original  # type: ignore[assignment]

    assert result.text == ""
    assert result.errors, "an unreadable page must be reported, not silently skipped"
    assert "ocr unavailable" in result.errors[0]


def test_missing_tesseract_binary_is_reported_not_raised() -> None:
    from PIL import Image

    from blade_detection.config import DetectionConfig
    from blade_detection.ocr import extract_text, tesseract_backend

    def _no_binary(*_args: object, **_kwargs: object) -> tuple[str, float]:
        raise OSError("tesseract is not installed or it's not in your PATH")

    original = tesseract_backend.run
    tesseract_backend.run = _no_binary  # type: ignore[assignment]
    try:
        result = extract_text([Image.new("RGB", (40, 20), "white")], DetectionConfig())
    finally:
        tesseract_backend.run = original  # type: ignore[assignment]

    assert result.errors
    assert result.text == ""


def _png_with_declared_size(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )


def test_password_protected_pdf_is_reported_not_treated_as_empty() -> None:
    fitz = _fitz()
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), PHI_LINE)
    data = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )

    result = extract_bytes(data, "discharge.pdf", "application/pdf", "a1")

    assert result.text == ""
    assert result.errors
    assert any(ExtractionIssue.PDF_ENCRYPTED in error for error in result.errors)


def test_pdf_beyond_the_page_cap_reports_the_truncation() -> None:
    fitz = _fitz()
    document = fitz.open()
    for page_number in range(1, MAX_PDF_PAGES + 11):
        page = document.new_page()
        filler = f"Ward round notes for page {page_number}, nothing sensitive on this page."
        page.insert_text((72, 72), filler)
        if page_number == MAX_PDF_PAGES + 5:
            page.insert_text((72, 96), PHI_LINE)
    data = document.tobytes()

    result = extract_bytes(data, "long.pdf", "application/pdf", "a1")

    assert "0024518" not in result.text
    assert result.errors
    assert any(ExtractionIssue.PDF_PAGE_LIMIT in error for error in result.errors)


def test_corrupt_docx_is_reported_not_treated_as_empty() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "this is not valid word xml")

    result = extract_bytes(
        buffer.getvalue(),
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "a1",
    )

    assert result.text == ""
    assert result.errors
    assert any(ExtractionIssue.DOCX_OPEN_FAILED in error for error in result.errors)


def test_corrupt_xlsx_is_reported_not_treated_as_empty() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "this is not valid spreadsheet xml")

    result = extract_bytes(
        buffer.getvalue(),
        "list.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "a1",
    )

    assert result.text == ""
    assert result.errors
    assert any(ExtractionIssue.SPREADSHEET_OPEN_FAILED in error for error in result.errors)


def test_oversized_image_is_reported_not_silently_skipped() -> None:
    data = _png_with_declared_size(12_000, 12_000)

    result = extract_bytes(data, "scan.png", "image/png", "a1")

    assert result.ocr_sources == []
    assert result.errors
    assert any(ExtractionIssue.IMAGE_TOO_LARGE in error for error in result.errors)


def test_unreadable_image_is_reported_not_silently_skipped() -> None:
    result = extract_bytes(b"\x89PNG\r\n\x1a\ntruncated", "scan.png", "image/png", "a1")

    assert result.ocr_sources == []
    assert result.errors


HTML_TABLE = (
    "<html><head><style>td{color:red}</style></head><body>"
    "<table><tr><td>UHID</td><td>0024518</td></tr>"
    "<tr><td>Name</td><td>Ramesh Kumar Iyer</td></tr></table>"
    "<script>var tracker = 1;</script></body></html>"
)


def test_html_attachment_is_stripped_to_text() -> None:
    result = extract_bytes(HTML_TABLE.encode("utf-8"), "summary.html", "text/html", "a1")

    assert "<td>" not in result.text
    assert "var tracker" not in result.text
    assert "td{color:red}" not in result.text
    assert "UHID 0024518" in result.text


def test_html_table_cells_still_yield_an_mrn() -> None:
    result = detect_email(
        EmailPayload(body=to_text(HTML_TABLE)),
    )
    assert "MRN" in {entity.type for entity in result.entities}


def test_html_email_part_is_stripped_not_appended_raw() -> None:
    message = EmailMessage()
    message["Subject"] = "Lab result"
    message.set_content(
        "<html><body><table><tr><td>UHID</td><td>0024518</td></tr></table></body></html>",
        subtype="html",
    )

    result = extract_bytes(bytes(message), "result.eml", "message/rfc822", "a1")

    assert "<td>" not in result.text
    assert "UHID 0024518" in result.text


def test_multipart_alternative_prefers_the_plain_text_part() -> None:
    message = EmailMessage()
    message["Subject"] = "Discharge summary"
    message.set_content("Plain part: UHID 0024518 for the patient.")
    message.add_alternative(
        "<html><body><p>Rich part: UHID 0099887 for the patient.</p></body></html>",
        subtype="html",
    )

    result = extract_bytes(bytes(message), "note.eml", "message/rfc822", "a1")

    assert "0024518" in result.text
    assert "0099887" not in result.text


def test_html_entities_are_decoded() -> None:
    markup = "<p>UHID&nbsp;0024518 &amp; ICD E11.9</p>"
    assert "&nbsp;" not in to_text(markup)
    assert "&amp;" not in to_text(markup)
    assert "0024518" in to_text(markup)
