from __future__ import annotations

import random

from blade_detection.recognition.patterns import RawMatch
from blade_detection.recognition.resolution import resolve_overlaps


def _match(start: int, end: int, priority: int, confidence: float) -> RawMatch:
    return RawMatch(
        entity_type="MRN",
        rule_name="synthetic",
        value="x" * (end - start),
        start=start,
        end=end,
        confidence=confidence,
        priority=priority,
        had_context=False,
    )


def _reference(matches: list[RawMatch]) -> list[RawMatch]:
    ordered = sorted(
        matches,
        key=lambda match: (match.priority, match.end - match.start, match.confidence),
        reverse=True,
    )
    kept: list[RawMatch] = []
    for candidate in ordered:
        if any(
            candidate.start < accepted.end and accepted.start < candidate.end for accepted in kept
        ):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda match: (match.start, match.end))


def test_the_sweep_keeps_the_highest_ranked_of_two_overlapping_matches() -> None:
    low = _match(0, 10, priority=10, confidence=0.9)
    high = _match(5, 12, priority=50, confidence=0.6)
    assert resolve_overlaps([low, high]) == [high]


def test_touching_spans_are_not_treated_as_overlapping() -> None:
    left = _match(0, 10, priority=10, confidence=0.9)
    right = _match(10, 20, priority=10, confidence=0.9)
    assert resolve_overlaps([left, right]) == [left, right]


def test_the_sweep_agrees_with_the_quadratic_reference() -> None:
    generator = random.Random(20260904)
    for _ in range(200):
        matches: list[RawMatch] = []
        for _ in range(generator.randint(0, 60)):
            start = generator.randint(0, 400)
            end = start + generator.randint(1, 25)
            matches.append(
                _match(
                    start,
                    end,
                    priority=generator.choice([20, 29, 30, 35, 45, 50, 55, 58, 60, 70]),
                    confidence=round(generator.uniform(0.5, 1.0), 2),
                )
            )
        assert resolve_overlaps(list(matches)) == _reference(list(matches))
