from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from importlib import import_module
from io import BytesIO
from math import sqrt
from typing import Any, Protocol, cast

import structlog
from PIL import Image

from blade_detection.extractors.issues import ExtractionIssue, no_issues, unique_issues

logger = structlog.get_logger(__name__)

LOW_TEXT_CHARS = 50
MAX_PDF_PAGES = 50
PDF_BASE_DPI = 72
OCR_TARGET_DPI = 300
MAX_RENDER_PIXELS = 25_000_000


class _Pixmap(Protocol):
    def tobytes(self, output: str) -> bytes: ...


class _Rect(Protocol):
    @property
    def width(self) -> float: ...

    @property
    def height(self) -> float: ...


class _PdfPage(Protocol):
    def get_text(self, option: str = "text") -> str: ...

    def get_images(self, full: bool = False) -> list[object]: ...

    def get_pixmap(self, **kwargs: object) -> _Pixmap: ...

    @property
    def rect(self) -> _Rect: ...


class _PdfDocument(Protocol):
    def __iter__(self) -> Iterator[_PdfPage]: ...

    def close(self) -> None: ...

    @property
    def needs_pass(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PdfPageText:
    text: str
    render_ocr_image: Callable[[], Image.Image | None] | None = None


def _no_pages() -> list[PdfPageText]:
    return []


@dataclass(frozen=True, slots=True)
class PdfContent:
    pages: list[PdfPageText] = field(default_factory=_no_pages)
    issues: list[str] = field(default_factory=no_issues)


def extract_pages(data: bytes) -> PdfContent:
    try:
        fitz = cast(Any, import_module("fitz"))
        document = cast(_PdfDocument, fitz.open(stream=data, filetype="pdf"))
    except Exception:
        logger.warning("pdf attachment open failed")
        return PdfContent(issues=[ExtractionIssue.PDF_OPEN_FAILED])

    pages: list[PdfPageText] = []
    issues: list[str] = []
    try:
        if getattr(document, "needs_pass", False):
            logger.warning("pdf attachment is password protected")
            return PdfContent(issues=[ExtractionIssue.PDF_ENCRYPTED])
        for page_index, page in enumerate(document):
            if page_index >= MAX_PDF_PAGES:
                logger.warning("pdf attachment page limit reached", max_pages=MAX_PDF_PAGES)
                issues.append(ExtractionIssue.PDF_PAGE_LIMIT)
                break
            text = page.get_text("text").strip()
            if len(text) >= LOW_TEXT_CHARS:
                pages.append(PdfPageText(text=text))
                continue
            zoom = _ocr_zoom(page)
            if zoom < _target_zoom():
                logger.warning(
                    "pdf page rendered below the target ocr resolution",
                    page=page_index + 1,
                )
                issues.append(ExtractionIssue.PDF_RESOLUTION_REDUCED)
            pages.append(
                PdfPageText(
                    text=text,
                    render_ocr_image=partial(render_page, data, page_index, zoom),
                )
            )
    except Exception:
        logger.warning("pdf attachment extraction failed")
        issues.append(ExtractionIssue.PDF_PAGE_UNREADABLE)
    finally:
        document.close()
    return PdfContent(pages=pages, issues=unique_issues(issues))


def _target_zoom() -> float:
    return OCR_TARGET_DPI / PDF_BASE_DPI


def _ocr_zoom(page: _PdfPage) -> float:
    target = _target_zoom()
    rect = page.rect
    pixels = (rect.width * target) * (rect.height * target)
    if pixels <= MAX_RENDER_PIXELS:
        return target
    return target * sqrt(MAX_RENDER_PIXELS / pixels)


def render_page(data: bytes, page_index: int, zoom: float) -> Image.Image | None:
    try:
        fitz = cast(Any, import_module("fitz"))
        document = fitz.open(stream=data, filetype="pdf")
        try:
            pixmap = cast(_Pixmap, document[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom)))
            encoded = pixmap.tobytes("png")
        finally:
            document.close()
        rendered = Image.open(BytesIO(encoded))
        rendered.load()
        return rendered if rendered.mode == "RGB" else rendered.convert("RGB")
    except Exception:
        logger.warning("pdf page render for OCR failed", page=page_index + 1)
        return None
