from __future__ import annotations

from html.parser import HTMLParser
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

BLOCK_TAGS: Final[frozenset[str]] = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "title",
        "tr",
        "ul",
    }
)

CELL_TAGS: Final[frozenset[str]] = frozenset({"td", "th"})

SKIPPED_TAGS: Final[frozenset[str]] = frozenset({"script", "style"})


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skipped_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in SKIPPED_TAGS:
            self._skipped_depth += 1
            return
        self._chunks.append(_separator(tag))

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self._skipped_depth = max(0, self._skipped_depth - 1)
            return
        self._chunks.append(_separator(tag))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self._chunks.append(_separator(tag))

    def handle_data(self, data: str) -> None:
        if self._skipped_depth:
            return
        self._chunks.append(data)

    def handle_comment(self, data: str) -> None:
        if self._skipped_depth:
            return
        self._chunks.append(f" {data} ")

    def collected(self) -> str:
        return "".join(self._chunks)


def _separator(tag: str) -> str:
    if tag in BLOCK_TAGS:
        return "\n"
    if tag in CELL_TAGS:
        return " "
    return ""


def _collapse(raw: str) -> str:
    collapsed: list[str] = []
    for line in raw.split("\n"):
        compact = " ".join(line.split())
        if compact or (collapsed and collapsed[-1]):
            collapsed.append(compact)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


def to_text(markup: str) -> str:
    collector = _TextCollector()
    try:
        collector.feed(markup)
        collector.close()
    except Exception:
        logger.warning("html part could not be parsed; falling back to the raw markup")
        return markup
    return _collapse(collector.collected())
