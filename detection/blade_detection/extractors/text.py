from __future__ import annotations

import csv
from dataclasses import dataclass, field
from importlib import import_module
from io import StringIO
from typing import Any, cast

import structlog

from blade_detection.extractors.issues import ExtractionIssue, no_issues

logger = structlog.get_logger(__name__)

MAX_EXTRACTED_CHARS = 1_000_000


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
    issues: list[str] = field(default_factory=no_issues)


def decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        chardet = cast(Any, import_module("chardet"))
        detected = chardet.detect(data[:100_000])
        encoding = detected.get("encoding")
        if isinstance(encoding, str):
            return data.decode(encoding, errors="replace")
    except Exception:
        logger.warning("text encoding detection failed")
    return data.decode("utf-8", errors="replace")


def extract(data: bytes, *, filename: str = "") -> TextContent:
    full = decode(data)
    decoded = full[:MAX_EXTRACTED_CHARS]
    issues: list[str] = [ExtractionIssue.TEXT_LIMIT] if len(full) > MAX_EXTRACTED_CHARS else []
    if _is_delimited(decoded, filename):
        rendered = extract_delimited(decoded)
        if rendered:
            return TextContent(rendered, issues)
    return TextContent(decoded, issues)


def _is_delimited(decoded: str, filename: str) -> bool:
    if filename.lower().endswith((".csv", ".tsv")):
        return True
    head = decoded[:2000]
    lines = [line for line in head.splitlines() if line.strip()][:5]
    if len(lines) < 2:
        return False
    counts = [line.count(",") for line in lines]
    return min(counts) >= 1 and len(set(counts)) == 1


def extract_delimited(decoded: str) -> str:
    from blade_detection.extractors.tabular import render_rows

    try:
        sample = decoded[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    try:
        rows = list(csv.reader(StringIO(decoded), dialect))
    except csv.Error:
        return ""
    return "\n".join(render_rows(rows))[:MAX_EXTRACTED_CHARS]


def extract_rtf(data: bytes) -> TextContent:
    try:
        striprtf = cast(Any, import_module("striprtf.striprtf"))
        rendered = cast(str, striprtf.rtf_to_text(decode(data), errors="ignore"))
    except Exception:
        logger.warning("rtf attachment extraction failed")
        return TextContent("", [ExtractionIssue.RTF_UNREADABLE])
    issues: list[str] = [ExtractionIssue.TEXT_LIMIT] if len(rendered) > MAX_EXTRACTED_CHARS else []
    return TextContent(rendered[:MAX_EXTRACTED_CHARS], issues)
