from __future__ import annotations

import re
import time
from functools import lru_cache
from itertools import pairwise
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

MAX_PATTERN_CHARS: Final[int] = 200
MAX_PROBE_SECONDS: Final[float] = 0.02

PROBE_UNITS: Final[tuple[str, ...]] = ("a", "0", "A0", "-", " ", "\t")
PROBE_LENGTHS: Final[tuple[int, ...]] = (12, 18, 21, 22, 24, 64, 128)
PROBE_REPEATS: Final[int] = 3
PROBE_NOISE_FLOOR_SECONDS: Final[float] = 0.00001
MAX_GROWTH_EXPONENT: Final[float] = 1.5

_COUNTED_REPEAT: Final[re.Pattern[str]] = re.compile(r"\{(\d+)(,(\d*))?\}")


def _group_spans(pattern: str) -> list[tuple[int, int]]:
    stack: list[int] = []
    spans: list[tuple[int, int]] = []
    inside_class = False
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            index += 2
            continue
        if inside_class:
            if character == "]":
                inside_class = False
        elif character == "[":
            inside_class = True
        elif character == "(":
            stack.append(index)
        elif character == ")" and stack:
            spans.append((stack.pop(), index))
        index += 1
    return spans


def _quantifier_at(pattern: str, index: int) -> str | None:
    if index >= len(pattern):
        return None
    character = pattern[index]
    if character in {"*", "+"}:
        return character
    if character != "{":
        return None
    match = _COUNTED_REPEAT.match(pattern, index)
    return match.group(0) if match else None


def _is_unbounded(quantifier: str) -> bool:
    if quantifier in {"*", "+"}:
        return True
    match = _COUNTED_REPEAT.fullmatch(quantifier)
    return bool(match and match.group(2) and not match.group(3))


def _repeats_more_than_once(quantifier: str) -> bool:
    if _is_unbounded(quantifier):
        return True
    match = _COUNTED_REPEAT.fullmatch(quantifier)
    if match is None:
        return False
    upper = match.group(3) or match.group(1)
    return int(upper) > 1


def _contains_unbounded_repeat(body: str) -> bool:
    inside_class = False
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 2
            continue
        if inside_class:
            if character == "]":
                inside_class = False
            index += 1
            continue
        if character == "[":
            inside_class = True
            index += 1
            continue
        quantifier = _quantifier_at(body, index)
        if quantifier is not None and _is_unbounded(quantifier):
            return True
        index += 1
    return False


def _group_body(pattern: str, start: int, end: int) -> str:
    body = pattern[start + 1 : end]
    if body.startswith("?"):
        head = body[1:2]
        if head in {":", "=", "!", ">"}:
            return body[2:]
        if head == "<":
            closing = body.find(">")
            return body[closing + 1 :] if closing >= 0 else body
        if head in {"i", "m", "s", "x", "a", "u", "L"}:
            colon = body.find(":")
            return body[colon + 1 :] if colon >= 0 else body
    return body


def _split_alternatives(body: str) -> list[str]:
    branches: list[str] = []
    depth = 0
    inside_class = False
    current: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            current.append(body[index : index + 2])
            index += 2
            continue
        if inside_class:
            if character == "]":
                inside_class = False
        elif character == "[":
            inside_class = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "|" and depth == 0:
            branches.append("".join(current))
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    branches.append("".join(current))
    return branches


def _has_overlapping_alternatives(body: str) -> bool:
    branches = _split_alternatives(body)
    if len(branches) < 2:
        return False
    for outer_index, outer in enumerate(branches):
        for inner_index, inner in enumerate(branches):
            if outer_index == inner_index or not outer or not inner:
                continue
            if inner.startswith(outer):
                return True
    return False


def structural_risks(pattern: str) -> list[str]:
    risks: list[str] = []
    for start, end in _group_spans(pattern):
        quantifier = _quantifier_at(pattern, end + 1)
        if quantifier is None or not _repeats_more_than_once(quantifier):
            continue
        body = _group_body(pattern, start, end)
        if _contains_unbounded_repeat(body):
            risks.append(f"nested quantifier in group at {start}")
        if _is_unbounded(quantifier) and _has_overlapping_alternatives(body):
            risks.append(f"overlapping repeated alternatives in group at {start}")
    return risks


def _search_seconds(compiled: re.Pattern[str], text: str) -> float:
    fastest = float("inf")
    for _ in range(PROBE_REPEATS):
        started = time.perf_counter()
        compiled.search(text)
        fastest = min(fastest, time.perf_counter() - started)
    return fastest


def _probe_seconds_by_length(compiled: re.Pattern[str]) -> dict[int, float]:
    timings: dict[int, float] = {}
    for length in PROBE_LENGTHS:
        slowest = 0.0
        for unit in PROBE_UNITS:
            text = (unit * length)[:length] + "!"
            slowest = max(slowest, _search_seconds(compiled, text))
        timings[length] = slowest
        if slowest > MAX_PROBE_SECONDS:
            return timings
    return timings


def _grows_superlinearly(timings: dict[int, float]) -> tuple[int, int] | None:
    lengths = sorted(timings)
    for shorter, longer in pairwise(lengths):
        baseline = timings[shorter]
        if baseline < PROBE_NOISE_FLOOR_SECONDS:
            continue
        allowed = baseline * (longer / shorter) ** MAX_GROWTH_EXPONENT
        if timings[longer] > allowed:
            return shorter, longer
    return None


@lru_cache(maxsize=256)
def is_safe_pattern(pattern: str) -> bool:
    if not 0 < len(pattern) <= MAX_PATTERN_CHARS:
        logger.warning("tenant pattern rejected on length", length=len(pattern))
        return False

    risks = structural_risks(pattern)
    if risks:
        logger.warning("tenant pattern rejected as structurally unsafe", risks=risks)
        return False

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning("tenant pattern rejected as invalid")
        return False

    timings = _probe_seconds_by_length(compiled)
    slowest = max(timings.values())
    if slowest > MAX_PROBE_SECONDS:
        logger.warning("tenant pattern rejected on a timing probe", seconds=round(slowest, 4))
        return False

    growth = _grows_superlinearly(timings)
    if growth is not None:
        logger.warning(
            "tenant pattern rejected on superlinear probe growth",
            shorter=growth[0],
            longer=growth[1],
        )
        return False
    return True
