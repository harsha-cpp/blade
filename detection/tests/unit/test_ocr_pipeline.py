from __future__ import annotations

import glob
import io
import shutil
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any, cast

import pytest
from PIL import Image, ImageDraw, ImageFont

from blade_detection.api import detect_email
from blade_detection.config import DetectionConfig
from blade_detection.extractors.issues import ExtractionIssue
from blade_detection.extractors.pdf import (
    LOW_TEXT_CHARS,
    OCR_IMAGE_COVERAGE,
    OCR_IMAGE_MIN_AREA_POINTS,
    extract_pages,
    image_coverage,
    needs_ocr,
    visible_placement_areas,
)
from blade_detection.models import Attachment, EmailPayload
from blade_detection.ocr import extract_text, paddle_backend, tesseract_backend

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary is not installed on this host",
)

LINES = [
    "DISCHARGE SUMMARY",
    "Patient: Lakshmi Devi",
    "UHID 0024518",
    "Aadhaar 7534 7930 7460",
    "Diagnosis E11.9",
]

PRINTED_HEADER = "APOLLO SPECIALITY HOSPITAL, CHENNAI - DISCHARGE SUMMARY PAGE 1 OF 1 (PRINTED)"


def _render_scan(path: Path) -> None:
    candidates = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) or glob.glob(
        "/System/Library/Fonts/**/*.ttf", recursive=True
    )
    font = ImageFont.truetype(candidates[0], 34) if candidates else ImageFont.load_default()

    image = Image.new("RGB", (1400, 400), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(LINES):
        draw.text((30, 30 + index * 68), line, fill="black", font=font)
    image.save(path)


@requires_tesseract
def test_scanned_document_is_read_and_scored(tmp_path: Path) -> None:
    path = tmp_path / "scan.png"
    _render_scan(path)

    result = detect_email(
        EmailPayload(
            attachments=[
                Attachment(
                    id="a1",
                    filename="scan.png",
                    mime_type="image/png",
                    size_bytes=path.stat().st_size,
                    sha256="x",
                    local_path=str(path),
                )
            ]
        )
    )

    assert result.ocr_pages == 1
    assert not result.extraction_errors
    found = {entity.type for entity in result.entities}
    assert "IN_AADHAAR" in found or "MRN" in found
    assert result.risk_score > 50


@requires_tesseract
def test_blank_page_is_not_reported_as_an_error(tmp_path: Path) -> None:
    path = tmp_path / "blank.png"
    Image.new("RGB", (800, 400), "white").save(path)

    result = detect_email(
        EmailPayload(
            attachments=[
                Attachment(
                    id="a1",
                    filename="blank.png",
                    mime_type="image/png",
                    size_bytes=path.stat().st_size,
                    sha256="x",
                    local_path=str(path),
                )
            ]
        )
    )

    assert result.entities == []
    assert not result.extraction_errors


def test_document_deadline_is_reported_not_silently_skipped() -> None:
    result = extract_text(
        [Image.new("RGB", (40, 20), "white")],
        DetectionConfig(),
        deadline=monotonic() - 1.0,
    )

    assert result.pages == 0
    assert ExtractionIssue.OCR_DEADLINE_REACHED in result.errors


def test_page_timeout_is_reported_and_the_next_page_is_still_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _timeout_then_text(*_args: object, **_kwargs: object) -> tuple[str, float]:
        calls.append(len(calls))
        if len(calls) == 1:
            raise tesseract_backend.OcrPageTimeoutError("tesseract timed out on a page")
        return "Aadhaar 7534 7930 7460", 0.9

    def _no_fallback(*_args: object, **_kwargs: object) -> tuple[str, float]:
        return "", 0.0

    monkeypatch.setattr(tesseract_backend, "run", _timeout_then_text)
    monkeypatch.setattr(paddle_backend, "run", _no_fallback)

    result = extract_text(
        [Image.new("RGB", (40, 20), "white"), Image.new("RGB", (40, 20), "white")],
        DetectionConfig(),
    )

    assert ExtractionIssue.OCR_PAGE_TIMEOUT in result.errors
    assert result.pages == 1
    assert "7534 7930 7460" in result.text


def test_tesseract_timeout_is_raised_as_a_page_timeout() -> None:
    module = cast(Any, import_module("pytesseract"))
    original = module.image_to_data

    def _timeout(*_args: object, **_kwargs: object) -> dict[str, list[object]]:
        raise RuntimeError("Tesseract process timeout")

    module.image_to_data = _timeout
    try:
        with pytest.raises(tesseract_backend.OcrPageTimeoutError):
            tesseract_backend.run(Image.new("RGB", (40, 20), "white"), DetectionConfig())
    finally:
        module.image_to_data = original


def _page_image(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_page(header: str, image_box: tuple[float, float, float, float] | None) -> bytes:
    fitz = cast(Any, import_module("fitz"))
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((40, 60), header)
    if image_box is not None:
        page.insert_image(fitz.Rect(*image_box), stream=_page_image(900, 1200))
    return cast(bytes, document.tobytes())


def test_a_printed_header_over_a_scanned_image_is_still_queued_for_ocr() -> None:
    assert len(PRINTED_HEADER) > LOW_TEXT_CHARS

    content = extract_pages(_pdf_page(PRINTED_HEADER, (40, 120, 555, 780)))

    assert len(content.pages) == 1
    assert content.pages[0].text.startswith("APOLLO")
    assert content.pages[0].render_ocr_image is not None


def test_a_text_only_page_is_not_queued_for_ocr() -> None:
    content = extract_pages(_pdf_page(PRINTED_HEADER, None))

    assert len(content.pages) == 1
    assert content.pages[0].render_ocr_image is None


def test_a_small_logo_does_not_queue_a_text_page_for_ocr() -> None:
    content = extract_pages(_pdf_page(PRINTED_HEADER, (500, 20, 560, 80)))

    assert len(content.pages) == 1
    assert content.pages[0].render_ocr_image is None


def _pdf_page_geometry(
    header: str,
    image_box: tuple[float, float, float, float],
    *,
    rotation: int = 0,
    origin: tuple[float, float] = (0.0, 0.0),
    image_size: tuple[int, int] = (900, 1200),
) -> bytes:
    fitz = cast(Any, import_module("fitz"))
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    left, top = origin
    if origin != (0.0, 0.0):
        page.set_mediabox(fitz.Rect(left, top, left + 595, top + 842))
    page.insert_text((left + 40, top + 60), header)
    page.insert_text((left + 40, top + 80), header)
    page.insert_image(
        fitz.Rect(
            left + image_box[0],
            top + image_box[1],
            left + image_box[2],
            top + image_box[3],
        ),
        stream=_page_image(*image_size),
    )
    if rotation:
        page.set_rotation(rotation)
    return cast(bytes, document.tobytes())


def _first_page(data: bytes) -> tuple[Any, Any]:
    fitz = cast(Any, import_module("fitz"))
    document = fitz.open(stream=data, filetype="pdf")
    return document, document[0]


def test_a_rotated_scan_below_the_unrotated_page_height_is_queued_for_ocr() -> None:
    document, page = _first_page(
        _pdf_page_geometry(PRINTED_HEADER, (40, 620, 555, 820), rotation=90, image_size=(1030, 400))
    )
    try:
        text = page.get_text("text").strip()
        assert len(text) > LOW_TEXT_CHARS
        assert image_coverage(page) >= OCR_IMAGE_COVERAGE
        assert needs_ocr(page, text)
    finally:
        document.close()


def test_a_scan_on_an_offset_mediabox_is_queued_for_ocr() -> None:
    document, page = _first_page(
        _pdf_page_geometry(PRINTED_HEADER, (40, 120, 555, 780), origin=(400.0, 500.0))
    )
    try:
        text = page.get_text("text").strip()
        assert len(text) > LOW_TEXT_CHARS
        assert image_coverage(page) >= OCR_IMAGE_COVERAGE
        assert needs_ocr(page, text)
    finally:
        document.close()


def test_an_id_card_sized_scan_is_queued_for_ocr_despite_low_coverage() -> None:
    document, page = _first_page(
        _pdf_page_geometry(PRINTED_HEADER, (40, 120, 220, 255), image_size=(800, 600))
    )
    try:
        text = page.get_text("text").strip()
        assert len(text) > LOW_TEXT_CHARS
        placed = max(visible_placement_areas(page))
        assert placed >= OCR_IMAGE_MIN_AREA_POINTS
        assert image_coverage(page) < OCR_IMAGE_COVERAGE
        assert needs_ocr(page, text)
    finally:
        document.close()


def test_a_small_logo_stays_below_the_absolute_area_trigger() -> None:
    document, page = _first_page(
        _pdf_page_geometry(PRINTED_HEADER, (500, 20, 560, 80), image_size=(600, 600))
    )
    try:
        text = page.get_text("text").strip()
        assert max(visible_placement_areas(page)) < OCR_IMAGE_MIN_AREA_POINTS
        assert not needs_ocr(page, text)
    finally:
        document.close()


def test_page_rotation_does_not_change_the_measured_coverage() -> None:
    measured: list[float] = []
    for rotation in (0, 90, 180, 270):
        document, page = _first_page(
            _pdf_page_geometry(PRINTED_HEADER, (40, 120, 555, 780), rotation=rotation)
        )
        try:
            measured.append(round(image_coverage(page), 6))
        finally:
            document.close()
    assert len(set(measured)) == 1


def _scanned_pdf(page_count: int) -> bytes:
    fitz = cast(Any, import_module("fitz"))
    document = fitz.open()
    for _ in range(page_count):
        page = document.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(40, 120, 555, 780), stream=_page_image(900, 1200))
    return cast(bytes, document.tobytes())


def test_a_multi_page_ocr_render_opens_the_document_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fitz = cast(Any, import_module("fitz"))
    content = extract_pages(_scanned_pdf(3))
    renders = [page.render_ocr_image for page in content.pages if page.render_ocr_image]
    assert len(renders) == 3

    opens: list[int] = []
    real_open = fitz.open

    def counting_open(*args: object, **kwargs: object) -> object:
        opens.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(fitz, "open", counting_open)
    images = [render() for render in renders]

    assert all(image is not None for image in images)
    assert len(opens) == 1


def test_image_coverage_reads_the_placed_area() -> None:
    fitz = cast(Any, import_module("fitz"))
    document = fitz.open(stream=_pdf_page(PRINTED_HEADER, (40, 120, 555, 780)), filetype="pdf")
    try:
        covered = image_coverage(document[0])
    finally:
        document.close()

    assert covered > OCR_IMAGE_COVERAGE
    assert covered <= 1.0
