from __future__ import annotations

from typing import Final

DEFAULT_VISIBLE_CHARS: Final[int] = 4

FULLY_MASKED_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ICD10",
        "DATE_OF_BIRTH",
        "PERSON",
    }
)

DOMAIN_ONLY_TYPES: Final[frozenset[str]] = frozenset({"EMAIL_ADDRESS"})


def mask_value(value: str, entity_type: str | None = None) -> str:
    compact = value.strip()
    if not compact:
        return ""
    if entity_type is None:
        return _masked_tail(compact, DEFAULT_VISIBLE_CHARS)
    if entity_type in FULLY_MASKED_TYPES:
        return "*" * len(compact)
    if entity_type in DOMAIN_ONLY_TYPES:
        return _domain_only(compact)
    return _masked_tail(compact, min(DEFAULT_VISIBLE_CHARS, len(compact) // 3))


def _masked_tail(compact: str, visible: int) -> str:
    if visible <= 0 or len(compact) <= visible:
        return "*" * len(compact)
    return f"{'*' * (len(compact) - visible)}{compact[-visible:]}"


def _domain_only(compact: str) -> str:
    at_sign = compact.rfind("@")
    if at_sign <= 0 or at_sign == len(compact) - 1:
        return "*" * len(compact)
    return f"{'*' * at_sign}{compact[at_sign:]}"
