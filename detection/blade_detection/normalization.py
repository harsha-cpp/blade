from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

ZERO_WIDTH_CHARACTERS: Final[frozenset[str]] = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})

DECIMAL_DIGIT_BLOCK_STARTS: Final[tuple[int, ...]] = (
    0x0660,
    0x06F0,
    0x07C0,
    0x0966,
    0x09E6,
    0x0A66,
    0x0AE6,
    0x0B66,
    0x0BE6,
    0x0C66,
    0x0CE6,
    0x0D66,
    0x0DE6,
    0x0E50,
    0x0ED0,
    0x0F20,
    0x1040,
    0x1090,
    0x17E0,
    0x1810,
    0x1946,
    0x19D0,
    0x1A80,
    0x1A90,
    0x1B50,
    0x1BB0,
    0x1C40,
    0x1C50,
    0xA620,
    0xA8D0,
    0xA900,
    0xA9D0,
    0xA9F0,
    0xAA50,
    0xABF0,
    0xFF10,
)


@dataclass(frozen=True, slots=True)
class FoldedText:
    text: str
    source: str
    offsets: tuple[int, ...] | None

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if self.offsets is None:
            return start, end
        return self.offsets[start], self.offsets[end]

    def original_slice(self, start: int, end: int) -> str:
        source_start, source_end = self.original_span(start, end)
        return self.source[source_start:source_end]


def ascii_digit(character: str) -> str | None:
    code_point = ord(character)
    for start in DECIMAL_DIGIT_BLOCK_STARTS:
        offset = code_point - start
        if 0 <= offset <= 9:
            return str(offset)
    return None


def _fold_character(character: str) -> str:
    folded: list[str] = []
    for candidate in unicodedata.normalize("NFKC", character):
        if candidate in ZERO_WIDTH_CHARACTERS:
            continue
        folded.append(ascii_digit(candidate) or candidate)
    return "".join(folded)


def fold(text: str) -> FoldedText:
    if text.isascii():
        return FoldedText(text=text, source=text, offsets=None)

    folded: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        for produced in _fold_character(character):
            folded.append(produced)
            offsets.append(index)
    offsets.append(len(text))
    return FoldedText(text="".join(folded), source=text, offsets=tuple(offsets))


NAME_LIKE_TYPES: Final[frozenset[str]] = frozenset(
    {"PERSON", "EMAIL_ADDRESS", "ABHA_ADDRESS", "IN_UPI"}
)

_FNV_OFFSET_BASIS: Final[int] = 0x811C9DC5
_FNV_PRIME: Final[int] = 0x01000193
_FNV_MASK: Final[int] = 0xFFFFFFFF


def normalise_value(entity_type: str, value: str) -> str:
    compact = value.strip().upper()
    if entity_type in NAME_LIKE_TYPES:
        return " ".join(compact.split())
    return "".join(character for character in compact if character.isalnum())


def value_fingerprint(entity_type: str, value: str) -> str:
    digest = _FNV_OFFSET_BASIS
    for byte in normalise_value(entity_type, value).encode("utf-8"):
        digest = ((digest ^ byte) * _FNV_PRIME) & _FNV_MASK
    return f"{digest:08x}"
