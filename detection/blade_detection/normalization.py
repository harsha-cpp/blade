from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

_INVISIBLE_CHARACTERS: Final[tuple[str, ...]] = (
    "\u00ad",
    "\u034f",
    "\u180e",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
)

_INVISIBLE_RANGES: Final[tuple[tuple[int, int], ...]] = ((0x2061, 0x2064), (0xFE00, 0xFE0F))

ZERO_WIDTH_CHARACTERS: Final[frozenset[str]] = frozenset(
    set(_INVISIBLE_CHARACTERS)
    | {chr(code) for first, last in _INVISIBLE_RANGES for code in range(first, last + 1)}
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


MAX_CODE_POINT: Final[int] = 0x110000


@lru_cache(maxsize=1)
def decimal_digits() -> dict[str, str]:
    return {
        character: str(unicodedata.decimal(character))
        for character in map(chr, range(MAX_CODE_POINT))
        if unicodedata.category(character) == "Nd"
    }


@lru_cache(maxsize=1)
def decimal_digit_block_starts() -> tuple[int, ...]:
    return tuple(
        sorted(ord(character) for character, digit in decimal_digits().items() if digit == "0")
    )


def ascii_digit(character: str) -> str | None:
    return decimal_digits().get(character)


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
