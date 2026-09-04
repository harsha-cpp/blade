from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_email_policy
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

import structlog
from PIL import Image

from blade_detection.extractors import docx, html, image, pdf, pptx, text, xlsx
from blade_detection.extractors.budget import ExtractionBudget
from blade_detection.extractors.issues import ExtractionIssue, unique_issues
from blade_detection.models import Attachment

logger = structlog.get_logger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLS_MIME = "application/vnd.ms-excel"

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_EMBEDDED_DEPTH = 2
CONTAINER_KINDS = frozenset({"archive", "email"})
OCR_CAPABLE_KINDS = frozenset({"pdf", "docx", "image", "archive", "email"})

_EXTENSION_KINDS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xls": "xlsx",
    ".pptx": "pptx",
    ".csv": "text",
    ".tsv": "text",
    ".txt": "text",
    ".log": "text",
    ".json": "text",
    ".md": "text",
    ".html": "html",
    ".htm": "html",
    ".rtf": "rtf",
    ".eml": "email",
    ".zip": "archive",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
    ".gif": "image",
}


class _MagicModule(Protocol):
    def from_buffer(self, buffer: bytes, *, mime: bool) -> str: ...


@dataclass(frozen=True, slots=True)
class OcrImageSource:
    load: Callable[[], Image.Image | None]
    unreadable_issue: str


def _no_sources() -> list[OcrImageSource]:
    return []


def _no_errors() -> list[str]:
    return []


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    ocr_sources: list[OcrImageSource] = field(default_factory=_no_sources)
    errors: list[str] = field(default_factory=_no_errors)


def _errors(attachment_id: str, issues: Iterable[str]) -> list[str]:
    return [f"{attachment_id}: {issue}" for issue in unique_issues(issues)]


def _append_chunk(chunks: list[str], budget: ExtractionBudget, chunk: str) -> None:
    if not chunk:
        return
    if chunks:
        chunks.append(budget.take_characters("\n"))
    chunks.append(chunk)


def _ready_source(loaded: Image.Image) -> OcrImageSource:
    return OcrImageSource(
        load=lambda: loaded,
        unreadable_issue=ExtractionIssue.IMAGE_UNREADABLE,
    )


def _budget_can_serve(kind: str, budget: ExtractionBudget) -> bool:
    if budget.characters > 0:
        return True
    return budget.ocr_images > 0 and kind in OCR_CAPABLE_KINDS


def _within_budget(
    result: ExtractionResult,
    budget: ExtractionBudget,
    attachment_id: str,
) -> ExtractionResult:
    kept_text = budget.take_characters(result.text)
    allowed_images = budget.take_ocr_images(len(result.ocr_sources))
    issues: list[str] = []
    if len(kept_text) < len(result.text):
        logger.warning("extraction character budget exhausted", attachment_id=attachment_id)
        issues.append(ExtractionIssue.EXTRACTION_CHAR_LIMIT)
    if allowed_images < len(result.ocr_sources):
        logger.warning("ocr image budget exhausted", attachment_id=attachment_id)
        issues.append(ExtractionIssue.OCR_IMAGE_LIMIT)
    if not issues:
        return result
    return ExtractionResult(
        kept_text,
        result.ocr_sources[:allowed_images],
        [*result.errors, *_errors(attachment_id, issues)],
    )


def extract_attachment(
    attachment: Attachment,
    *,
    budget: ExtractionBudget | None = None,
) -> ExtractionResult:
    if attachment.local_path is None:
        return ExtractionResult(
            "", [], _errors(attachment.id, [ExtractionIssue.MISSING_LOCAL_PATH])
        )

    path = Path(attachment.local_path)
    try:
        if path.stat().st_size > MAX_ATTACHMENT_BYTES:
            return ExtractionResult(
                "", [], _errors(attachment.id, [ExtractionIssue.ATTACHMENT_TOO_LARGE])
            )
        data = path.read_bytes()
    except OSError:
        logger.warning("attachment read failed", attachment_id=attachment.id)
        return ExtractionResult("", [], _errors(attachment.id, [ExtractionIssue.READ_FAILED]))

    return extract_bytes(
        data,
        attachment.filename,
        attachment.mime_type,
        attachment.id,
        budget=budget,
    )


def extract_bytes(
    data: bytes,
    filename: str,
    declared_mime: str,
    attachment_id: str,
    *,
    depth: int = 0,
    budget: ExtractionBudget | None = None,
) -> ExtractionResult:
    remaining = budget if budget is not None else ExtractionBudget()
    kind = _classify(data, filename, declared_mime)
    if kind is None:
        logger.warning(
            "unsupported attachment type",
            attachment_id=attachment_id,
            filename=filename,
            mime_type=declared_mime,
        )
        return ExtractionResult(
            "", [], _errors(attachment_id, [ExtractionIssue.UNSUPPORTED_FILE_TYPE])
        )

    if not _budget_can_serve(kind, remaining):
        logger.warning(
            "extraction budget exhausted before attachment",
            attachment_id=attachment_id,
            kind=kind,
        )
        return ExtractionResult(
            "", [], _errors(attachment_id, [ExtractionIssue.EXTRACTION_BUDGET_EXHAUSTED])
        )

    try:
        result = _extract_kind(kind, data, filename, attachment_id, depth, remaining)
    except Exception:
        logger.warning("attachment extraction failed", attachment_id=attachment_id, kind=kind)
        return ExtractionResult("", [], _errors(attachment_id, [ExtractionIssue.EXTRACTION_FAILED]))

    if kind in CONTAINER_KINDS:
        return result
    return _within_budget(result, remaining, attachment_id)


def _extract_kind(
    kind: str,
    data: bytes,
    filename: str,
    attachment_id: str,
    depth: int,
    budget: ExtractionBudget,
) -> ExtractionResult:
    if kind == "pdf":
        document = pdf.extract_pages(data)
        return ExtractionResult(
            text="\n".join(page.text for page in document.pages if page.text),
            ocr_sources=[
                OcrImageSource(
                    load=page.render_ocr_image,
                    unreadable_issue=ExtractionIssue.PDF_RENDER_FAILED,
                )
                for page in document.pages
                if page.render_ocr_image is not None
            ],
            errors=_errors(attachment_id, document.issues),
        )
    if kind == "docx":
        content = docx.extract(data)
        return ExtractionResult(
            content.text,
            [_ready_source(embedded) for embedded in content.images],
            _errors(attachment_id, content.issues),
        )
    if kind == "xlsx":
        workbook = xlsx.extract(data)
        return ExtractionResult(workbook.text, [], _errors(attachment_id, workbook.issues))
    if kind == "pptx":
        presentation = pptx.extract(data)
        return ExtractionResult(presentation.text, [], _errors(attachment_id, presentation.issues))
    if kind == "text":
        plain = text.extract(data, filename=filename)
        return ExtractionResult(plain.text, [], _errors(attachment_id, plain.issues))
    if kind == "html":
        markup = text.extract(data, filename=filename)
        return ExtractionResult(
            html.to_text(markup.text), [], _errors(attachment_id, markup.issues)
        )
    if kind == "rtf":
        rich = text.extract_rtf(data)
        return ExtractionResult(rich.text, [], _errors(attachment_id, rich.issues))
    if kind == "image":
        opened = image.open_image(data)
        return ExtractionResult(
            "",
            [_ready_source(opened.image)] if opened.image is not None else [],
            _errors(attachment_id, opened.issues),
        )
    if kind == "email":
        return _extract_email(data, attachment_id, depth, budget)
    if kind == "archive":
        return _extract_archive(data, attachment_id, depth, budget)
    return ExtractionResult("", [], _errors(attachment_id, [ExtractionIssue.UNSUPPORTED_FILE_TYPE]))


_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF87a", "image"),
    (b"GIF89a", "image"),
    (b"BM", "image"),
    (b"II*\x00", "image"),
    (b"MM\x00*", "image"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "xlsx"),
    (b"{\\rtf", "rtf"),
)

_ZIP_MEMBER_KINDS: tuple[tuple[str, str], ...] = (
    ("word/", "docx"),
    ("xl/", "xlsx"),
    ("ppt/", "pptx"),
)


def _sniff_signature(data: bytes) -> str | None:
    for prefix, kind in _SIGNATURES:
        if data.startswith(prefix):
            return kind
    if data.startswith(b"PK\x03\x04"):
        return _zip_kind(data)
    return None


def _zip_kind(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
        for prefix, kind in _ZIP_MEMBER_KINDS:
            if any(name.startswith(prefix) for name in names):
                return kind
    except Exception:
        return "archive"
    return "archive"


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for byte in sample if byte >= 32 or byte in (9, 10, 13))
    return printable / len(sample) > 0.95


def _classify(data: bytes, filename: str, declared_mime: str) -> str | None:
    signature = _sniff_signature(data)
    if signature is not None:
        if signature == "archive":
            by_extension = _EXTENSION_KINDS.get(Path(filename).suffix.lower())
            return by_extension if by_extension is not None else "archive"
        return signature

    sniffed = _detect_mime(data, declared_mime)
    for mime in (sniffed, declared_mime.lower()):
        kind = _kind_from_mime(mime)
        if kind is not None:
            if kind == "archive":
                by_extension = _EXTENSION_KINDS.get(Path(filename).suffix.lower())
                if by_extension is not None:
                    return by_extension
            return kind
    by_extension = _EXTENSION_KINDS.get(Path(filename).suffix.lower())
    if by_extension is not None:
        return by_extension
    return "text" if _looks_like_text(data) else None


def _kind_from_mime(mime: str) -> str | None:
    normalized = mime.split(";")[0].strip().lower()
    if not normalized:
        return None
    if normalized == "application/pdf":
        return "pdf"
    if normalized == DOCX_MIME:
        return "docx"
    if normalized in {XLSX_MIME, XLS_MIME}:
        return "xlsx"
    if normalized == PPTX_MIME:
        return "pptx"
    if normalized in {"text/html", "application/xhtml+xml"}:
        return "html"
    if normalized in {"application/rtf", "text/rtf"}:
        return "rtf"
    if normalized in {"message/rfc822", "application/vnd.ms-outlook"}:
        return "email"
    if normalized in {"application/zip", "application/x-zip-compressed"}:
        return "archive"
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("text/") or normalized in {
        "application/json",
        "application/csv",
    }:
        return "text"
    return None


def _detect_mime(data: bytes, fallback: str) -> str:
    try:
        magic = cast(_MagicModule, import_module("magic"))
        return magic.from_buffer(data[:4096], mime=True) or fallback
    except Exception:
        return fallback


def _leaf_parts(message: Message[str, str]) -> list[Message[str, str]]:
    if not message.is_multipart():
        return [message]
    payload = message.get_payload()
    if not isinstance(payload, list):
        return [message]
    parts = [
        cast("Message[str, str]", part)
        for part in cast(list[object], payload)
        if isinstance(part, Message)
    ]
    if message.get_content_type() == "multipart/alternative":
        preferred = _preferred_alternative(parts)
        return _leaf_parts(preferred) if preferred is not None else []
    leaves: list[Message[str, str]] = []
    for part in parts:
        leaves.extend(_leaf_parts(part))
    return leaves


def _preferred_alternative(
    parts: list[Message[str, str]],
) -> Message[str, str] | None:
    if not parts:
        return None
    for part in parts:
        if part.get_content_type() == "text/plain" and not part.get_filename():
            return part
    return parts[-1]


def _extract_email(
    data: bytes,
    attachment_id: str,
    depth: int,
    budget: ExtractionBudget,
) -> ExtractionResult:
    if depth >= MAX_EMBEDDED_DEPTH:
        return ExtractionResult(
            "", [], _errors(attachment_id, [ExtractionIssue.EMBEDDED_DEPTH_LIMIT])
        )

    message = message_from_bytes(data, policy=default_email_policy)
    chunks: list[str] = []
    sources: list[OcrImageSource] = []
    errors: list[str] = []

    headers = "\n".join(
        f"{header}: {message.get(header)}"
        for header in ("subject", "from", "to", "cc")
        if message.get(header)
    )
    kept_headers = budget.take_characters(headers)
    _append_chunk(chunks, budget, kept_headers)
    if len(kept_headers) < len(headers):
        errors.extend(_errors(attachment_id, [ExtractionIssue.EXTRACTION_CHAR_LIMIT]))

    for part in _leaf_parts(message):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        if budget.characters <= 0:
            logger.warning("email character budget exhausted", attachment_id=attachment_id)
            errors.extend(_errors(attachment_id, [ExtractionIssue.EXTRACTION_CHAR_LIMIT]))
            break
        if not budget.take_member():
            logger.warning("email part budget exhausted", attachment_id=attachment_id)
            errors.extend(_errors(attachment_id, [ExtractionIssue.EMAIL_PART_LIMIT]))
            break
        if not budget.take_bytes(len(payload)):
            logger.warning("email unpack budget exhausted", attachment_id=attachment_id)
            errors.extend(_errors(attachment_id, [ExtractionIssue.EMAIL_UNPACK_LIMIT]))
            break
        content_type = part.get_content_type()
        filename = part.get_filename() or ""
        part_id = f"{attachment_id}:{filename or content_type}"
        if content_type in {"text/plain", "text/html"} and not filename:
            decoded = text.decode(payload)
            if content_type == "text/html":
                decoded = html.to_text(decoded)
            kept = budget.take_characters(decoded)
            _append_chunk(chunks, budget, kept)
            if len(kept) < len(decoded):
                errors.extend(_errors(part_id, [ExtractionIssue.EXTRACTION_CHAR_LIMIT]))
            continue
        nested = extract_bytes(
            payload,
            filename,
            content_type,
            part_id,
            depth=depth + 1,
            budget=budget,
        )
        _append_chunk(chunks, budget, nested.text)
        sources.extend(nested.ocr_sources)
        errors.extend(nested.errors)

    return ExtractionResult("".join(chunks), sources, errors)


def _extract_archive(
    data: bytes,
    attachment_id: str,
    depth: int,
    budget: ExtractionBudget,
) -> ExtractionResult:
    if depth >= MAX_EMBEDDED_DEPTH:
        return ExtractionResult(
            "", [], _errors(attachment_id, [ExtractionIssue.EMBEDDED_DEPTH_LIMIT])
        )

    chunks: list[str] = []
    sources: list[OcrImageSource] = []
    errors: list[str] = []

    with zipfile.ZipFile(BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if budget.characters <= 0:
                logger.warning("archive character budget exhausted", attachment_id=attachment_id)
                errors.extend(_errors(attachment_id, [ExtractionIssue.EXTRACTION_CHAR_LIMIT]))
                break
            if not budget.take_member():
                logger.warning("archive member budget exhausted", attachment_id=attachment_id)
                errors.extend(_errors(attachment_id, [ExtractionIssue.ARCHIVE_MEMBER_LIMIT]))
                break
            if not budget.take_bytes(info.file_size):
                logger.warning("archive unpack budget exhausted", attachment_id=attachment_id)
                errors.extend(_errors(attachment_id, [ExtractionIssue.ARCHIVE_UNPACK_LIMIT]))
                break
            try:
                member = archive.read(info)
            except Exception:
                errors.append(
                    f"{attachment_id}: {ExtractionIssue.ARCHIVE_MEMBER_UNREADABLE}: {info.filename}"
                )
                continue
            nested = extract_bytes(
                member,
                info.filename,
                "application/octet-stream",
                f"{attachment_id}:{info.filename}",
                depth=depth + 1,
                budget=budget,
            )
            _append_chunk(chunks, budget, nested.text)
            sources.extend(nested.ocr_sources)
            errors.extend(nested.errors)

    return ExtractionResult("".join(chunks), sources, errors)
