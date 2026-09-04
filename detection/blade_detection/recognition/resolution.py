from __future__ import annotations

from blade_detection.recognition.patterns import RawMatch


def _overlaps(left: RawMatch, right: RawMatch) -> bool:
    return left.start < right.end and right.start < left.end


def _rank(match: RawMatch) -> tuple[int, int, float]:
    return (match.priority, match.end - match.start, match.confidence)


def resolve_overlaps(matches: list[RawMatch]) -> list[RawMatch]:
    ordered = sorted(matches, key=_rank, reverse=True)
    kept: list[RawMatch] = []
    for candidate in ordered:
        if any(_overlaps(candidate, accepted) for accepted in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda match: (match.start, match.end))
