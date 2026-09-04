from __future__ import annotations

import pytest

from blade_detection.api import detect_email
from blade_detection.config import DetectionConfig, RecognizerConfig
from blade_detection.models import EmailPayload
from blade_detection.recognition import custom_mrn_rules
from blade_detection.recognition.pattern_safety import (
    MAX_PATTERN_CHARS,
    is_safe_pattern,
    structural_risks,
)


@pytest.mark.parametrize(
    "pattern",
    [
        r"MRN-\d{4,8}",
        r"(?:UHID|MRN)[ -]?\d{4,10}",
        r"HOSP-(\d{4})-(\d{3,6})",
        r"[A-Z]{3}/\d{2}/\d{5}",
    ],
)
def test_ordinary_tenant_patterns_are_accepted(pattern: str) -> None:
    assert is_safe_pattern(pattern) is True
    assert structural_risks(pattern) == []


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+$",
        r"(a*)*b",
        r"(\d+)+X",
        r"(x+x+)+y",
        r"(?:[a-z]+){2,}z",
        r"([A-Za-z0-9]+\s?)*!",
    ],
)
def test_nested_quantifiers_are_rejected(pattern: str) -> None:
    assert structural_risks(pattern)
    assert is_safe_pattern(pattern) is False


@pytest.mark.parametrize("pattern", [r"(a|a)+b", r"(a|ab)+c", r"(?:UH|UHID)*Z"])
def test_overlapping_repeated_alternatives_are_rejected(pattern: str) -> None:
    assert structural_risks(pattern)
    assert is_safe_pattern(pattern) is False


def test_a_bounded_but_explosive_pattern_is_caught_by_the_timing_probe() -> None:
    pattern = r"(?:a?){22}a{22}"
    assert structural_risks(pattern) == []
    assert is_safe_pattern(pattern) is False


def test_invalid_and_oversized_patterns_are_rejected() -> None:
    assert is_safe_pattern("[") is False
    assert is_safe_pattern("") is False
    assert is_safe_pattern("A" * (MAX_PATTERN_CHARS + 1)) is False


def _config(patterns: list[str]) -> DetectionConfig:
    return DetectionConfig(recognizers=RecognizerConfig(custom_mrn_patterns=patterns))


def test_unsafe_tenant_patterns_are_dropped_before_compiling() -> None:
    rules = custom_mrn_rules(_config([r"(a+)+$", r"MRN-\d{4,8}"]))
    assert [rule.pattern for rule in rules] == [r"MRN-\d{4,8}"]


def test_a_safe_tenant_pattern_still_detects() -> None:
    result = detect_email(
        EmailPayload(body="Record HOSP-2026-004417 filed today."),
        _config([r"HOSP-\d{4}-\d{6}"]),
    )
    assert "MRN" in {entity.type for entity in result.entities}


def test_an_unsafe_tenant_pattern_does_not_stall_a_scan() -> None:
    result = detect_email(
        EmailPayload(body="a" * 200 + " Record HOSP-2026-004417 filed today."),
        _config([r"(a+)+$"]),
    )
    assert result.duration_ms < 5000
