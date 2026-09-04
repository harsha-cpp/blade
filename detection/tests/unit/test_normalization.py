from __future__ import annotations

import re
from pathlib import Path

from blade_detection.api import detect_email
from blade_detection.models import EmailPayload
from blade_detection.normalization import (
    ZERO_WIDTH_CHARACTERS,
    ascii_digit,
    decimal_digit_block_starts,
    fold,
)

NORMALIZATION_TS = (
    Path(__file__).parents[3]
    / "frontend"
    / "packages"
    / "shared"
    / "src"
    / "detection"
    / "normalization.ts"
)

ADLAM_ZERO = 0x1E950


def _browser_source() -> str:
    return NORMALIZATION_TS.read_text(encoding="utf-8")


def _array_body(source: str, name: str) -> str:
    match = re.search(rf"const {name}[^=]*= \[(.*?)\];", source, re.DOTALL)
    assert match is not None, f"{name} not found in normalization.ts"
    return match.group(1)


def _browser_block_starts(source: str) -> tuple[int, ...]:
    body = _array_body(source, "DECIMAL_DIGIT_BLOCK_STARTS")
    return tuple(int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", body))


def _browser_invisible_characters(source: str) -> frozenset[str]:
    singles = _array_body(source, "INVISIBLE_CHARACTERS")
    characters = {chr(int(value, 16)) for value in re.findall(r"\\u([0-9a-fA-F]{4})", singles)}
    ranges = _array_body(source, "INVISIBLE_RANGES")
    for first, last in re.findall(r"\[(0x[0-9a-fA-F]+), (0x[0-9a-fA-F]+)\]", ranges):
        characters |= {chr(code) for code in range(int(first, 16), int(last, 16) + 1)}
    return frozenset(characters)


def test_every_decimal_digit_block_is_derived_from_unicodedata() -> None:
    starts = decimal_digit_block_starts()
    assert ord("0") in starts
    assert ADLAM_ZERO in starts
    for start in starts:
        for offset in range(10):
            assert ascii_digit(chr(start + offset)) == str(offset)


def test_the_browser_digit_table_matches_the_python_derivation() -> None:
    assert _browser_block_starts(_browser_source()) == decimal_digit_block_starts(), (
        "the browser digit table is stale; regenerate normalization.ts from "
        "blade_detection.normalization.decimal_digit_block_starts()"
    )


def test_the_browser_invisible_characters_match_the_python_set() -> None:
    assert _browser_invisible_characters(_browser_source()) == ZERO_WIDTH_CHARACTERS


def test_an_adlam_digit_identifier_folds_to_ascii() -> None:
    adlam = "".join(chr(ADLAM_ZERO + int(digit)) for digit in "753479307460")
    folded = fold(f"Aadhaar {adlam} issued.")
    assert "753479307460" in folded.text


def test_an_adlam_digit_aadhaar_is_detected_with_original_offsets() -> None:
    adlam = "".join(chr(ADLAM_ZERO + int(digit)) for digit in "753479307460")
    body = f"Aadhaar {adlam} issued."
    result = detect_email(EmailPayload(body=body))
    aadhaar = [entity for entity in result.entities if entity.type == "IN_AADHAAR"]
    assert len(aadhaar) == 1
    entity = aadhaar[0]
    assert entity.start is not None and entity.end is not None
    assert body[entity.start : entity.end] == adlam
