from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Final

from blade_detection.recognition.reference_data import IFSC_BANK_CODES, WHO_ICD10_CATEGORIES

_VERHOEFF_D: Final[tuple[tuple[int, ...], ...]] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_VERHOEFF_P: Final[tuple[tuple[int, ...], ...]] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def verhoeff_ok(value: str) -> bool:
    digits = _digits(value)
    if not digits:
        return False
    checksum = 0
    for index, char in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][int(char)]]
    return checksum == 0


def _is_repdigit(digits: str) -> bool:
    return len(set(digits)) == 1


def _is_sequential(digits: str) -> bool:
    pairs = list(itertools.pairwise(digits))
    ascending = all(int(b) - int(a) == 1 for a, b in pairs)
    descending = all(int(a) - int(b) == 1 for a, b in pairs)
    return ascending or descending


def validate_aadhaar(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 12:
        return False
    if digits[0] in {"0", "1"}:
        return False
    if _is_repdigit(digits) or _is_sequential(digits):
        return False
    if len(set(digits)) <= 2:
        return False
    return verhoeff_ok(digits)


_PAN_HOLDER_TYPES: Final[frozenset[str]] = frozenset("PCHABGJLFTEK")
_PAN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_KNOWN_PLACEHOLDER_PANS: Final[frozenset[str]] = frozenset(
    {"ABCDE1234F", "AAAAA0000A", "AAAAA1111A", "ABCDE0000A", "XXXXX0000X", "AAAPL1234C"}
)


def validate_pan(value: str) -> bool:
    pan = value.strip().upper()
    if not _PAN_RE.match(pan):
        return False
    if pan in _KNOWN_PLACEHOLDER_PANS:
        return False
    if pan[3] not in _PAN_HOLDER_TYPES:
        return False
    return len(set(pan[:5])) != 1


_GSTIN_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)
_GSTIN_ALPHABET: Final[str] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_gstin(value: str) -> bool:
    gstin = value.strip().upper()
    if not _GSTIN_RE.match(gstin):
        return False
    state = int(gstin[:2])
    if not 1 <= state <= 38:
        return False

    total = 0
    for index, char in enumerate(gstin[:14]):
        position = _GSTIN_ALPHABET.index(char)
        factor = 2 if index % 2 else 1
        product = position * factor
        total += product // 36 + product % 36
    check = (36 - total % 36) % 36
    return _GSTIN_ALPHABET[check] == gstin[14]


_IFSC_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def validate_ifsc(value: str) -> bool:
    return bool(_IFSC_RE.match(value.strip().upper()))


def validate_ifsc_known_bank(value: str) -> bool:
    ifsc = value.strip().upper()
    if not _IFSC_RE.match(ifsc):
        return False
    return ifsc[:4] in IFSC_BANK_CODES


_PASSPORT_RE: Final[re.Pattern[str]] = re.compile(r"^[A-PR-WY][0-9]{7}$")


def validate_passport(value: str) -> bool:
    return bool(_PASSPORT_RE.match(value.strip().upper()))


_STATE_CODES: Final[frozenset[str]] = frozenset(
    {
        "AN",
        "AP",
        "AR",
        "AS",
        "BR",
        "CG",
        "CH",
        "DD",
        "DL",
        "DN",
        "GA",
        "GJ",
        "HP",
        "HR",
        "JH",
        "JK",
        "KA",
        "KL",
        "LA",
        "LD",
        "MH",
        "ML",
        "MN",
        "MP",
        "MZ",
        "NL",
        "OD",
        "OR",
        "PB",
        "PY",
        "RJ",
        "SK",
        "TN",
        "TR",
        "TS",
        "UK",
        "UP",
        "WB",
    }
)


def validate_driving_license(value: str) -> bool:
    compact = re.sub(r"[\s-]", "", value.strip().upper())
    if not re.match(r"^[A-Z]{2}[0-9]{13}$", compact):
        return False
    return compact[:2] in _STATE_CODES


_VOTER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}[0-9]{7}$")


def validate_voter_id(value: str) -> bool:
    return bool(_VOTER_RE.match(value.strip().upper()))


_ICD10_SUBCODE_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9][0-9A-Z]{0,3}$")


@lru_cache(maxsize=1)
def clinical_modification_categories() -> frozenset[str]:
    try:
        import simple_icd_10_cm as icd
    except ImportError:  # pragma: no cover - dependency is declared
        return frozenset()
    return frozenset(
        code for code in icd.get_all_codes(with_dots=False) if len(code) == 3 and code[0].isalpha()
    )


@lru_cache(maxsize=1)
def icd10_categories() -> frozenset[str]:
    return clinical_modification_categories() | WHO_ICD10_CATEGORIES


@lru_cache(maxsize=4096)
def _is_clinical_modification_item(dotless_code: str) -> bool:
    try:
        import simple_icd_10_cm as icd
    except ImportError:  # pragma: no cover - dependency is declared
        return True
    return bool(icd.is_valid_item(dotless_code))


def validate_icd10(value: str) -> bool:
    code = value.strip().upper()
    category, dot, subcode = code.partition(".")
    if dot and not _ICD10_SUBCODE_RE.match(subcode):
        return False
    if category in clinical_modification_categories():
        return _is_clinical_modification_item(category + subcode)
    return category in WHO_ICD10_CATEGORIES


def validate_in_phone(value: str) -> bool:
    digits = _digits(value)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return False
    if digits[0] not in {"6", "7", "8", "9"}:
        return False
    return not _is_repdigit(digits)


def validate_abha_number(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 14:
        return False
    if digits[0] == "0":
        return False
    return not _is_repdigit(digits)


def validate_bank_account(value: str) -> bool:
    digits = _digits(value)
    if not 9 <= len(digits) <= 18:
        return False
    return not _is_repdigit(digits)


def always_valid(value: str) -> bool:
    del value
    return True


VALIDATORS: Final[dict[str, Callable[[str], bool]]] = {
    "aadhaar": validate_aadhaar,
    "abha_number": validate_abha_number,
    "bank_account": validate_bank_account,
    "driving_license": validate_driving_license,
    "gstin": validate_gstin,
    "icd10": validate_icd10,
    "ifsc": validate_ifsc,
    "ifsc_known_bank": validate_ifsc_known_bank,
    "in_phone": validate_in_phone,
    "pan": validate_pan,
    "passport": validate_passport,
    "voter_id": validate_voter_id,
}


CHECKSUM_VALIDATORS: Final[frozenset[str]] = frozenset({"aadhaar", "gstin"})


def get_validator(name: str | None) -> Callable[[str], bool]:
    if name is None:
        return always_valid
    validator = VALIDATORS.get(name)
    if validator is None:
        raise KeyError(f"unknown validator: {name}")
    return validator
