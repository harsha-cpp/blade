from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class ExtractionIssue(StrEnum):
    MISSING_LOCAL_PATH = "missing local path"
    ATTACHMENT_TOO_LARGE = "attachment too large"
    READ_FAILED = "read failed"
    UNSUPPORTED_FILE_TYPE = "unsupported file type"
    EXTRACTION_FAILED = "extraction failed"
    EMBEDDED_DEPTH_LIMIT = "embedded depth limit"
    ARCHIVE_UNPACK_LIMIT = "archive unpack limit reached"
    ARCHIVE_MEMBER_LIMIT = "archive member limit reached"
    ARCHIVE_MEMBER_UNREADABLE = "archive member could not be read"
    EMAIL_PART_LIMIT = "email part limit reached"
    EMAIL_UNPACK_LIMIT = "email unpack limit reached"
    EXTRACTION_CHAR_LIMIT = "extraction truncated at the character limit"
    EXTRACTION_BUDGET_EXHAUSTED = "skipped because the extraction budget was exhausted"
    OCR_IMAGE_LIMIT = "ocr image limit reached"
    PDF_OPEN_FAILED = "pdf could not be opened"
    PDF_ENCRYPTED = "pdf is password protected"
    PDF_PAGE_LIMIT = "pdf truncated at the page limit"
    PDF_PAGE_UNREADABLE = "pdf pages could not be read"
    PDF_RENDER_FAILED = "pdf page could not be rendered for ocr"
    PDF_RESOLUTION_REDUCED = "pdf page rendered below the target ocr resolution"
    DOCX_OPEN_FAILED = "docx could not be opened"
    DOCX_UNREADABLE = "docx content could not be read"
    DOCX_TEXT_BOX_UNREADABLE = "docx text boxes could not be read"
    DOCX_IMAGE_UNREADABLE = "docx embedded images could not be read"
    DOCX_IMAGE_LIMIT = "docx truncated at the embedded image limit"
    DOCX_PART_LIMIT = "docx truncated at the inspected part limit"
    SPREADSHEET_OPEN_FAILED = "spreadsheet could not be opened"
    SPREADSHEET_UNREADABLE = "spreadsheet content could not be read"
    SPREADSHEET_CELL_LIMIT = "spreadsheet truncated at the cell limit"
    PRESENTATION_OPEN_FAILED = "presentation could not be opened"
    PRESENTATION_UNREADABLE = "presentation content could not be read"
    PRESENTATION_SLIDE_LIMIT = "presentation truncated at the slide limit"
    RTF_UNREADABLE = "rtf content could not be read"
    TEXT_LIMIT = "text truncated at the character limit"
    IMAGE_TOO_LARGE = "image exceeds the pixel limit"
    IMAGE_UNREADABLE = "image could not be decoded"


def unique_issues(issues: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for issue in issues:
        seen.setdefault(str(issue), None)
    return list(seen)


def no_issues() -> list[str]:
    return []
