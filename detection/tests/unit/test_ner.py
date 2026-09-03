from __future__ import annotations

import pytest

from blade_detection.recognition import ner

MODEL = "en_core_web_sm"
FILLER_LINE = "The ward round notes are unremarkable.\n"
NAME_LINE = "Dr. Ramesh Kumar Iyer signed the discharge summary.\n"


def _offsets_are_absolute(text: str, spans: list[ner.NerSpan]) -> bool:
    return all(text[span.start : span.end] == span.text for span in spans)


def test_a_title_immediately_before_a_single_name_promotes_it() -> None:
    spans = ner.person_spans("Dr. Menon reviewed the chart.", MODEL)
    assert [span.text for span in spans] == ["Menon"]


def test_a_single_name_without_a_title_is_not_reported() -> None:
    assert ner.person_spans("Menon reviewed the chart.", MODEL) == []


def test_a_distant_title_does_not_promote_a_later_name() -> None:
    text = (
        "Dr. Sunita Bhatt is on call. " + ("The ward is quiet tonight. " * 40) + "Apollo is closed."
    )
    spans = ner.person_spans(text, MODEL)
    assert [span.text for span in spans] == ["Sunita Bhatt"]
    assert _offsets_are_absolute(text, spans)


def test_the_chunk_size_never_exceeds_the_model_limit() -> None:
    assert ner.load_model(MODEL).max_length >= ner.NER_CHUNK_CHARS


def test_text_longer_than_the_model_limit_is_scanned_without_raising() -> None:
    text = NAME_LINE + "A" * (ner.load_model(MODEL).max_length + 1)

    spans = ner.person_spans(text, MODEL)

    assert spans
    assert _offsets_are_absolute(text, spans)


def test_entity_offsets_are_absolute_across_a_chunk_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ner, "NER_CHUNK_CHARS", 200)
    text = FILLER_LINE * 6 + NAME_LINE + FILLER_LINE * 6

    spans = ner.person_spans(text, MODEL)

    assert spans
    assert _offsets_are_absolute(text, spans)
    assert any(span.start > 200 for span in spans)
    assert any("Iyer" in span.text for span in spans)


def test_a_name_split_by_a_chunk_boundary_keeps_offsets_correct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "Ward round notes for the morning shift are unremarkable today. "
    text = prefix + "Ramesh Kumar Iyer was discharged after review."
    monkeypatch.setattr(ner, "NER_CHUNK_CHARS", len(prefix) + len("Ramesh "))
    monkeypatch.setattr(ner, "NER_CHUNK_BOUNDARY_SEARCH", 0)

    spans = ner.person_spans(text, MODEL)

    assert _offsets_are_absolute(text, spans)
    assert all(span.text != "Ramesh" for span in spans)


def test_chunk_offsets_cover_the_whole_text_without_overlap() -> None:
    text = FILLER_LINE * 40
    chunks = list(ner.chunk_offsets(text, 200))

    assert "".join(chunk for _, chunk in chunks) == text
    rebuilt = 0
    for offset, chunk in chunks:
        assert offset == rebuilt
        rebuilt += len(chunk)
    assert rebuilt == len(text)
