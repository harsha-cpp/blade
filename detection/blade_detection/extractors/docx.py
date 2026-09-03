from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import import_module
from io import BytesIO
from typing import Any, Protocol, cast

import structlog
from PIL import Image, UnidentifiedImageError

from blade_detection.extractors.issues import ExtractionIssue, no_issues, unique_issues

logger = structlog.get_logger(__name__)

MAX_EXTRACTED_CHARS = 1_000_000
MAX_EMBEDDED_IMAGES = 20
MAX_INSPECTED_PARTS = 20
_TEXT_XMLNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class _Paragraph(Protocol):
    text: str


class _Cell(Protocol):
    text: str


class _Row(Protocol):
    cells: Iterable[_Cell]


class _Table(Protocol):
    rows: Iterable[_Row]


def _empty_images() -> list[Image.Image]:
    return []


@dataclass(frozen=True, slots=True)
class DocxContent:
    text: str
    images: list[Image.Image] = field(default_factory=_empty_images)
    issues: list[str] = field(default_factory=no_issues)


def extract(data: bytes) -> DocxContent:
    chunks: list[str] = []
    issues: list[str] = []
    total = 0

    try:
        document_factory = cast(Callable[[BytesIO], Any], import_module("docx").Document)
        document = document_factory(BytesIO(data))
    except Exception:
        logger.warning("docx attachment open failed")
        return DocxContent("", issues=[ExtractionIssue.DOCX_OPEN_FAILED])

    try:
        for paragraph in cast(Iterable[_Paragraph], document.paragraphs):
            total = _append(chunks, paragraph.text, total)
        for table in cast(Iterable[_Table], document.tables):
            for row in table.rows:
                for cell in row.cells:
                    total = _append(chunks, cell.text, total)
        for section in cast(Iterable[Any], document.sections):
            for container in (section.header, section.footer):
                for paragraph in cast(Iterable[_Paragraph], container.paragraphs):
                    total = _append(chunks, paragraph.text, total)
    except Exception:
        logger.warning("docx attachment extraction failed")
        issues.append(ExtractionIssue.DOCX_UNREADABLE)

    if total >= MAX_EXTRACTED_CHARS:
        issues.append(ExtractionIssue.TEXT_LIMIT)

    textbox_chunks, textbox_issues = _textbox_text(data)
    chunks.extend(textbox_chunks)
    issues.extend(textbox_issues)

    images, image_issues = _embedded_images(data)
    issues.extend(image_issues)

    return DocxContent(
        "\n".join(chunk for chunk in chunks if chunk),
        images,
        unique_issues(issues),
    )


def _append(chunks: list[str], value: str, total: int) -> int:
    if not value or not value.strip():
        return total
    remaining = MAX_EXTRACTED_CHARS - total
    if remaining <= 0:
        return total
    chunk = value[:remaining]
    chunks.append(chunk)
    return total + len(chunk)


def _textbox_text(data: bytes) -> tuple[list[str], list[str]]:
    try:
        from xml.etree import ElementTree

        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.startswith("word/") and name.endswith(".xml")
            ]
            found: list[str] = []
            issues: list[str] = []
            if len(names) > MAX_INSPECTED_PARTS:
                issues.append(ExtractionIssue.DOCX_PART_LIMIT)
            for name in names[:MAX_INSPECTED_PARTS]:
                root = ElementTree.fromstring(archive.read(name))
                for node in root.iter(f"{{{_TEXT_XMLNS}}}txbxContent"):
                    text = "".join(
                        element.text or "" for element in node.iter(f"{{{_TEXT_XMLNS}}}t")
                    )
                    if text.strip():
                        found.append(text)
            return found, issues
    except Exception:
        logger.warning("docx text box extraction failed")
        return [], [ExtractionIssue.DOCX_TEXT_BOX_UNREADABLE]


def _embedded_images(data: bytes) -> tuple[list[Image.Image], list[str]]:
    images: list[Image.Image] = []
    issues: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            media = [
                name
                for name in archive.namelist()
                if name.startswith("word/media/")
                and name.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"))
            ]
            if len(media) > MAX_EMBEDDED_IMAGES:
                issues.append(ExtractionIssue.DOCX_IMAGE_LIMIT)
            for name in media[:MAX_EMBEDDED_IMAGES]:
                try:
                    image = Image.open(BytesIO(archive.read(name)))
                    image.load()
                    images.append(image.convert("RGB"))
                except (OSError, UnidentifiedImageError, ValueError):
                    issues.append(ExtractionIssue.DOCX_IMAGE_UNREADABLE)
                    continue
    except Exception:
        logger.warning("docx embedded image extraction failed")
        issues.append(ExtractionIssue.DOCX_IMAGE_UNREADABLE)
    return images, issues
