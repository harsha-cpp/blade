from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, cast

import spacy
import structlog
from spacy.language import Language

logger = structlog.get_logger(__name__)

NER_CONFIDENCE = 0.55
NER_PRIORITY = 20
NER_CHUNK_CHARS = 100_000
NER_CHUNK_BOUNDARY_SEARCH = 4_000
NER_CHUNK_OVERLAP_CHARS = 512
TITLE_LOOKBEHIND_CHARS = 16
UNUSED_PIPELINE_COMPONENTS = ("tagger", "parser", "attribute_ruler", "lemmatizer", "senter")

_TITLE_BEFORE = re.compile(
    r"(?:mr|mrs|ms|miss|dr|doctor|master|baby|smt|shri|sri|prof)\.?\s+$",
    re.IGNORECASE,
)


class _SpacyLoader(Protocol):
    def __call__(self, name: str, *, exclude: list[str]) -> Language: ...


class NlpModelUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NerSpan:
    text: str
    label: str
    start: int
    end: int


@lru_cache(maxsize=4)
def load_model(model_name: str) -> Language:
    loader = cast(_SpacyLoader, spacy.load)
    try:
        return loader(model_name, exclude=list(UNUSED_PIPELINE_COMPONENTS))
    except OSError as exc:
        raise NlpModelUnavailableError(
            f"spaCy model {model_name!r} is not installed. It is a declared "
            "dependency; run `uv sync` in the detection package."
        ) from exc


_NOT_NAMES: frozenset[str] = frozenset(
    {
        "aadhaar",
        "aadhar",
        "abha",
        "abdm",
        "ayushman",
        "uhid",
        "mrn",
        "gstin",
        "tpa",
        "nabh",
        "icd",
        "opd",
        "ipd",
        "uidai",
        "pan",
        "ifsc",
        "upi",
        "hba1c",
        "ecg",
        "ct",
        "mri",
        "bls",
        "cme",
        "his",
        "it",
        "hr",
        "gst",
        "patient",
        "hospital",
        "ward",
        "doctor",
        "nurse",
        "consultant",
        "diagnosis",
        "discharge",
        "admission",
        "policy",
        "claim",
        "insurance",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "his",
        "her",
        "their",
        "our",
        "this",
        "that",
        "does",
        "not",
        "no",
        "all",
        "any",
        "as",
        "it",
        "its",
    }
)


def looks_like_name(text: str) -> bool:
    tokens = text.split()
    if not 1 <= len(tokens) <= 4:
        return False
    for raw in tokens:
        token = raw.strip(".,;:()")
        if not token or not token.replace(".", "").isalpha():
            return False
        if not token[0].isupper():
            return False
        if token.isupper() and len(token) > 1:
            return False
        if token.lower() in _STOPWORDS or token.lower() in _NOT_NAMES:
            return False
    return True


def chunk_offsets(text: str, chunk_chars: int) -> Iterator[tuple[int, str]]:
    length = len(text)
    if length == 0:
        return
    overlap = min(NER_CHUNK_OVERLAP_CHARS, max(0, chunk_chars // 2))
    start = 0
    while True:
        end = min(start + chunk_chars, length)
        if end < length:
            end = _split_point(text, start, end)
        yield start, text[start:end]
        if end >= length:
            return
        start = max(start + 1, end - overlap)


def _split_point(text: str, start: int, end: int) -> int:
    floor = max(start + 1, end - NER_CHUNK_BOUNDARY_SEARCH)
    window = text[floor:end]
    for separator in ("\n", " "):
        index = window.rfind(separator)
        if index != -1:
            return floor + index + 1
    return end


def _preceded_by_title(text: str, start: int) -> bool:
    prefix = text[max(0, start - TITLE_LOOKBEHIND_CHARS) : start]
    return _TITLE_BEFORE.search(prefix) is not None


def person_spans(text: str, model_name: str) -> list[NerSpan]:
    if not text.strip():
        return []
    nlp = load_model(model_name)
    chunk_chars = min(NER_CHUNK_CHARS, nlp.max_length)
    spans: list[NerSpan] = []
    covered_to = 0
    for offset, chunk in chunk_offsets(text, chunk_chars):
        for entity in nlp(chunk).ents:
            start = offset + entity.start_char
            end = offset + entity.end_char
            if end <= covered_to:
                continue
            titled = _preceded_by_title(text, start)
            if entity.label_ != "PERSON" and not (entity.label_ == "ORG" and titled):
                continue
            if not looks_like_name(entity.text):
                continue
            if len(entity.text.split()) < 2 and not titled:
                continue
            spans.append(NerSpan(entity.text, "PERSON", start, end))
        covered_to = max(covered_to, offset + len(chunk))
    return spans
