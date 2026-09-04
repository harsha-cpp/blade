from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
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
OCR_IMAGE_COVERAGE = 0.1
OCR_IMAGE_MIN_AREA_POINTS = 15_000.0
MAX_PDF_PAGES = 50
PDF_BASE_DPI = 72
OCR_TARGET_DPI = 300
MAX_RENDER_PIXELS = 25_000_000

_Box = tuple[float, float, float, float]


class _Pixmap(Protocol):
    def tobytes(self, output: str) -> bytes: ...


class _Rect(Protocol):
    @property
    def x0(self) -> float: ...

    @property
    def y0(self) -> float: ...

    @property
    def x1(self) -> float: ...

    @property
    def y1(self) -> float: ...

    @property
    def width(self) -> float: ...

    @property
    def height(self) -> float: ...


class _PdfPage(Protocol):
    def get_text(self, option: str = "text") -> str: ...

    def get_image_info(self, hashes: bool = False, xrefs: bool = False) -> list[object]: ...

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


class _PageRenderer:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._document: Any | None = None
        self._pending = 0

    def expect_page(self) -> None:
        self._pending += 1

    def render(self, page_index: int, zoom: float) -> Image.Image | None:
        try:
            fitz = cast(Any, import_module("fitz"))
            page = self._opened()[page_index]
            pixmap = cast(_Pixmap, page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)))
            rendered = Image.open(BytesIO(pixmap.tobytes("png")))
            rendered.load()
            return rendered if rendered.mode == "RGB" else rendered.convert("RGB")
        except Exception:
            logger.warning("pdf page render for OCR failed", page=page_index + 1)
            return None
        finally:
            self._pending -= 1
            if self._pending <= 0:
                self.close()

    def _opened(self) -> Any:
        if self._document is None:
            fitz = cast(Any, import_module("fitz"))
            self._document = fitz.open(stream=self._data, filetype="pdf")
        return self._document

    def close(self) -> None:
        document = self._document
        self._document = None
        if document is not None:
            document.close()


def extract_pages(data: bytes) -> PdfContent:
    try:
        fitz = cast(Any, import_module("fitz"))
        document = cast(_PdfDocument, fitz.open(stream=data, filetype="pdf"))
    except Exception:
        logger.warning("pdf attachment open failed")
        return PdfContent(issues=[ExtractionIssue.PDF_OPEN_FAILED])

    pages: list[PdfPageText] = []
    issues: list[str] = []
    renderer = _PageRenderer(data)
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
            if not needs_ocr(page, text):
                pages.append(PdfPageText(text=text))
                continue
            zoom = _ocr_zoom(page)
            if zoom < _target_zoom():
                logger.warning(
                    "pdf page rendered below the target ocr resolution",
                    page=page_index + 1,
                )
                issues.append(ExtractionIssue.PDF_RESOLUTION_REDUCED)
            renderer.expect_page()
            pages.append(
                PdfPageText(
                    text=text,
                    render_ocr_image=partial(renderer.render, page_index, zoom),
                )
            )
    except Exception:
        logger.warning("pdf attachment extraction failed")
        issues.append(ExtractionIssue.PDF_PAGE_UNREADABLE)
    finally:
        document.close()
    return PdfContent(pages=pages, issues=unique_issues(issues))


def needs_ocr(page: _PdfPage, text: str) -> bool:
    if len(text) < LOW_TEXT_CHARS:
        return True
    areas = visible_placement_areas(page)
    if not areas:
        return False
    if _coverage(page, areas) >= OCR_IMAGE_COVERAGE:
        return True
    return max(areas) >= OCR_IMAGE_MIN_AREA_POINTS


def image_coverage(page: _PdfPage) -> float:
    return _coverage(page, visible_placement_areas(page))


def _coverage(page: _PdfPage, areas: list[float]) -> float:
    page_area = _box_area(_page_box(page))
    if page_area <= 0:
        return 0.0
    return min(1.0, sum(areas) / page_area)


def visible_placement_areas(page: _PdfPage) -> list[float]:
    page_box = _page_box(page)
    areas: list[float] = []
    for placement in _placements(page):
        box = _placement_box(placement)
        if box is None:
            continue
        area = _box_area(_intersection(_to_page_space(page, box), page_box))
        if area > 0:
            areas.append(area)
    return areas


def _placements(page: _PdfPage) -> list[object]:
    try:
        return page.get_image_info()
    except Exception:
        logger.warning("pdf page image inventory failed")
        return []


def _page_box(page: _PdfPage) -> _Box:
    rect = page.rect
    return (rect.x0, rect.y0, rect.x1, rect.y1)


def _box_area(box: _Box) -> float:
    left, top, right, bottom = box
    return max(0.0, right - left) * max(0.0, bottom - top)


def _intersection(box: _Box, clip: _Box) -> _Box:
    return (
        max(box[0], clip[0]),
        max(box[1], clip[1]),
        min(box[2], clip[2]),
        min(box[3], clip[3]),
    )


def _placement_box(placement: object) -> _Box | None:
    if not isinstance(placement, dict):
        return None
    box = cast(dict[str, object], placement).get("bbox")
    if not isinstance(box, list | tuple) or len(cast(Sequence[object], box)) != 4:
        return None
    edges = cast(Sequence[object], box)
    try:
        left, top, right, bottom = (float(cast(float, edge)) for edge in edges)
    except (TypeError, ValueError):
        return None
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def _to_page_space(page: _PdfPage, box: _Box) -> _Box:
    origin_x, origin_y = _mediabox_origin(page)
    a, b, c, d, e, f = _rotation_matrix(page)
    corners = ((box[0] - origin_x, box[1] - origin_y), (box[2] - origin_x, box[3] - origin_y))
    mapped = [(a * x + c * y + e, b * x + d * y + f) for x, y in corners]
    xs = [point[0] for point in mapped]
    ys = [point[1] for point in mapped]
    return (min(xs), min(ys), max(xs), max(ys))


def _mediabox_origin(page: _PdfPage) -> tuple[float, float]:
    mediabox = getattr(page, "mediabox", None)
    try:
        return float(getattr(mediabox, "x0", 0.0)), float(getattr(mediabox, "y0", 0.0))
    except (TypeError, ValueError):
        return 0.0, 0.0


def _rotation_matrix(page: _PdfPage) -> tuple[float, float, float, float, float, float]:
    matrix = getattr(page, "rotation_matrix", None)
    components = tuple(getattr(matrix, name, None) for name in ("a", "b", "c", "d", "e", "f"))
    if any(component is None for component in components):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    try:
        a, b, c, d, e, f = (float(cast(float, component)) for component in components)
    except (TypeError, ValueError):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return (a, b, c, d, e, f)


def _target_zoom() -> float:
    return OCR_TARGET_DPI / PDF_BASE_DPI


def _ocr_zoom(page: _PdfPage) -> float:
    target = _target_zoom()
    rect = page.rect
    pixels = (rect.width * target) * (rect.height * target)
    if pixels <= MAX_RENDER_PIXELS:
        return target
    return target * sqrt(MAX_RENDER_PIXELS / pixels)
