from __future__ import annotations

import json
from typing import cast

import pytest

from blade_detection.evaluation.parity import FIXTURE_PATH, build_fixture, render_fixture


def test_committed_parity_fixture_matches_the_python_engine() -> None:
    if not FIXTURE_PATH.exists():  # pragma: no cover - frontend not checked out
        pytest.skip(f"parity fixture not present at {FIXTURE_PATH}")

    committed = FIXTURE_PATH.read_text(encoding="utf-8")
    assert committed == render_fixture(), (
        "the committed parity fixture is stale. Regenerate it with:\n"
        "  python -m blade_detection.evaluation.parity"
    )


def test_fixture_covers_the_corpus_and_the_unicode_folding_cases() -> None:
    payload = build_fixture()
    identifiers = {case["id"] for case in payload["cases"]}

    assert len(payload["cases"]) >= 100
    assert "unicode.aadhaar-devanagari-digits" in identifiers
    assert "unicode.pan-zero-width-joiner" in identifiers
    assert payload["serverOnlyRules"] == ["spacy-ner", "mrn-tenant-*"]
    divergences = cast(list[str], payload["browserValidatorDivergences"])
    assert isinstance(divergences, list)
    assert any("icd10" in entry for entry in divergences)


def test_fixture_records_spans_that_index_the_original_text() -> None:
    payload = build_fixture()
    for case in payload["cases"]:
        text = case["text"]
        for entity in case["entities"]:
            assert 0 <= entity["start"] < entity["end"] <= len(text)


def test_fixture_file_is_plain_ascii_json() -> None:
    if not FIXTURE_PATH.exists():  # pragma: no cover - frontend not checked out
        pytest.skip(f"parity fixture not present at {FIXTURE_PATH}")

    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    assert raw.isascii()
    assert json.loads(raw)["cases"]
