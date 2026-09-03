from __future__ import annotations

import re
import time
from typing import Final

import pytest

from blade_detection.recognition.patterns import term_pattern
from blade_detection.rules import BUILTIN_RULES
from blade_detection.rules.schema import Rule

FILLER_LENGTH: Final[int] = 5000

MAX_SEARCH_SECONDS: Final[float] = 0.1

FILLERS: Final[tuple[tuple[str, str], ...]] = (
    ("spaces", " "),
    ("newlines", "\n"),
    ("tabs", "\t"),
)

FIXED_PREFIXES: Final[tuple[str, ...]] = (
    "patient",
    "policy",
    "specimen",
    "mrn",
    "uhid",
    "claim",
    "dob",
    "sample",
    "accession",
    "registration",
    "visit",
    "admission",
    "medical record",
    "account holder",
    "beneficiary",
    "referring",
    "hospital no",
    "nursing council",
    "for",
    "dr",
)

_LITERAL_RUN = re.compile(r"[A-Za-z][A-Za-z ]*")


def _leading_literals(pattern: str) -> frozenset[str]:
    stripped = pattern
    for prefix in (r"\b", "(?:", "("):
        while stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
    found: set[str] = set()
    for alternative in stripped.split("|"):
        match = _LITERAL_RUN.match(alternative)
        if match and len(match.group(0)) >= 2:
            found.add(match.group(0).lower())
    return frozenset(found)


def _pattern_cases() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = []
    for rule in BUILTIN_RULES:
        cases.append((f"{rule.name}.pattern", rule.pattern, rule.case_sensitive))
        if rule.continuation:
            cases.append((f"{rule.name}.continuation", rule.continuation, rule.case_sensitive))
    return cases


def _context_cases() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = []
    for rule in BUILTIN_RULES:
        for field in ("context_terms", "negative_terms"):
            terms: list[str] = getattr(rule, field)
            compiled = term_pattern(tuple(terms))
            if compiled is not None:
                cases.append((f"{rule.name}.{field}", compiled.pattern, False))
    return cases


def _adversarial_inputs(pattern: str) -> list[tuple[str, str]]:
    prefixes = set(FIXED_PREFIXES) | _leading_literals(pattern)
    inputs: list[tuple[str, str]] = []
    for prefix in sorted(prefixes):
        for filler_name, filler in FILLERS:
            inputs.append((f"{prefix!r}+{filler_name}", prefix + filler * FILLER_LENGTH + "A"))
    return inputs


def _slowest(name: str, pattern: str, case_sensitive: bool) -> tuple[str, float]:
    compiled = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    slowest_label = ""
    slowest_seconds = 0.0
    for label, text in _adversarial_inputs(pattern):
        started = time.perf_counter()
        compiled.search(text)
        elapsed = time.perf_counter() - started
        if elapsed > slowest_seconds:
            slowest_seconds = elapsed
            slowest_label = f"{name} on {label}"
    return slowest_label, slowest_seconds


ALL_CASES: Final[list[tuple[str, str, bool]]] = _pattern_cases() + _context_cases()


def test_every_rule_pattern_is_covered() -> None:
    covered = {name for name, _, _ in ALL_CASES}
    for rule in BUILTIN_RULES:
        assert f"{rule.name}.pattern" in covered


@pytest.mark.parametrize(("name", "pattern", "case_sensitive"), ALL_CASES)
def test_pattern_survives_whitespace_flooding(
    name: str, pattern: str, case_sensitive: bool
) -> None:
    label, elapsed = _slowest(name, pattern, case_sensitive)
    assert elapsed < MAX_SEARCH_SECONDS, (
        f"{label} took {elapsed * 1000:.1f} ms on a {FILLER_LENGTH} character run; "
        "bound the whitespace quantifiers in the rule pack"
    )


def test_rule_pack_has_no_unbounded_whitespace_runs() -> None:
    offenders: list[str] = []
    for rule in BUILTIN_RULES:
        fields: list[tuple[str, str | None]] = [
            ("pattern", rule.pattern),
            ("continuation", rule.continuation),
        ]
        for field, value in fields:
            if value and re.search(r"(?:\\s|\[[^\]]*\\s[^\]]*\])[*+]", value):
                offenders.append(f"{rule.name}.{field}")
    assert offenders == [], f"unbounded whitespace quantifiers remain in: {offenders}"


def test_labelled_rules_still_match_realistic_separators() -> None:
    by_name = {rule.name: rule for rule in BUILTIN_RULES}
    expectations: list[tuple[str, str, str]] = [
        ("mrn-labelled", "MRN: MH-2024-0098", "MH-2024-0098"),
        ("mrn-labelled", "UHID 4471203", "4471203"),
        ("mrn-labelled", "Medical Record No. - 4471203", "4471203"),
        ("mrn-labelled", "Hospital No:\n  4471203", "4471203"),
        ("visit-id-labelled", "IP No : IP-2024-3311", "IP-2024-3311"),
        ("lab-accession-labelled", "Specimen No: LAB2024991", "LAB2024991"),
        ("lab-accession-labelled", "Accession   #  LAB2024991", "LAB2024991"),
        ("policy-labelled", "Policy No. HDFC/2024/778812", "HDFC/2024/778812"),
        ("claim-labelled", "Claim ID - CLM90881234", "CLM90881234"),
        ("person-labelled", "Patient Name : Ravi Kumar", "Ravi Kumar"),
        ("person-titled", "Dr. Ravi Kumar", "Ravi Kumar"),
        ("practitioner-registration", "Medical Council Reg. No.: 88213", "88213"),
    ]
    for name, text, expected in expectations:
        rule: Rule = by_name[name]
        compiled = re.compile(rule.pattern, 0 if rule.case_sensitive else re.IGNORECASE)
        match = compiled.search(text)
        assert match is not None, f"{name} no longer matches {text!r}"
        assert match.group(rule.value_group) == expected
