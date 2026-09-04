from __future__ import annotations

from pathlib import Path

import pytest

from blade_detection.evaluation import (
    EvaluationReport,
    compare,
    evaluate_corpus,
    load_baseline,
    load_corpus,
    render,
)
from blade_detection.evaluation.taxonomy import CANONICAL_TYPES
from blade_detection.rules import BUILTIN_RULES

CORPUS_DIR = Path(__file__).parents[1] / "corpus"
BASELINE_PATH = Path(__file__).parents[1] / "accuracy_baseline.json"


@pytest.fixture(scope="module")
def report() -> EvaluationReport:
    return evaluate_corpus(CORPUS_DIR)


def test_corpus_loads_and_every_label_resolves() -> None:
    samples = load_corpus(CORPUS_DIR)
    assert len(samples) >= 100, "corpus has shrunk below a usable size"
    assert any(not sample.expect_phi for sample in samples), "no negative samples"
    assert any(sample.expect_phi for sample in samples), "no positive samples"


def test_no_accuracy_regression(report: EvaluationReport) -> None:
    regressions = compare(report, load_baseline(BASELINE_PATH))
    assert not regressions, (
        "accuracy regressed:\n"
        + "\n".join(f"  {regression}" for regression in regressions)
        + "\n\n"
        + render(report)
    )


def test_every_builtin_rule_emits_a_canonical_type() -> None:
    for rule in BUILTIN_RULES:
        assert rule.entity_type in CANONICAL_TYPES, (
            f"{rule.name} emits {rule.entity_type}, which the corpus taxonomy does not know"
        )
