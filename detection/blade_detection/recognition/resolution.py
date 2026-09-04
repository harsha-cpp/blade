from __future__ import annotations

from bisect import bisect_left

from blade_detection.recognition.patterns import RawMatch


def _rank(match: RawMatch) -> tuple[int, int, float]:
    return (match.priority, match.end - match.start, match.confidence)


def resolve_overlaps(matches: list[RawMatch]) -> list[RawMatch]:
    ordered = sorted(matches, key=_rank, reverse=True)
    accepted_starts: list[int] = []
    accepted: list[RawMatch] = []
    for candidate in ordered:
        position = bisect_left(accepted_starts, candidate.end)
        if position and accepted[position - 1].end > candidate.start:
            continue
        accepted_starts.insert(position, candidate.start)
        accepted.insert(position, candidate)
    return accepted
