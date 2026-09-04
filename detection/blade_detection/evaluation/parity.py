from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from blade_detection.config import DetectionConfig, NlpConfig
from blade_detection.evaluation.corpus import load_corpus
from blade_detection.models import Entity
from blade_detection.recognition import analyze
from blade_detection.rules import RULE_PACK_VERSION

CORPUS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "tests" / "corpus"

FIXTURE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "packages"
    / "shared"
    / "src"
    / "detection"
    / "parity-fixture.json"
)

SERVER_ONLY_RULES: Final[list[str]] = ["spacy-ner", "mrn-tenant-*"]

BROWSER_VALIDATOR_DIVERGENCES: Final[list[str]] = [
    "icd10: the browser accepts any subcode under a known category because the "
    "full ICD-10-CM item list is about 1 MB of JSON",
]

UNICODE_CASES: Final[tuple[tuple[str, str], ...]] = (
    (
        "unicode.aadhaar-devanagari-digits",
        "Aadhaar \u096d\u096b\u0969\u096a \u096d\u096f\u0969\u0966 "
        "\u096d\u096a\u096c\u0966 collected at the enrolment camp.",
    ),
    (
        "unicode.aadhaar-devanagari-label",
        "\u0906\u0927\u093e\u0930 7534 7930 7460 collected at the camp.",
    ),
    (
        "unicode.pan-zero-width-joiner",
        "PAN HKP\u200dPS5875Q filed with the form 16 return.",
    ),
    (
        "unicode.pan-zero-width-space",
        "PAN HKPPS\u200b5875Q filed with the income tax return.",
    ),
    (
        "unicode.uhid-fullwidth-digits",
        "UHID \uff10\uff10\uff12\uff14\uff15\uff11\uff18 admitted for treatment.",
    ),
    (
        "unicode.phone-devanagari-digits",
        "Patient mobile number \u096f\u096e\u096a\u096b\u0967\u0968"
        "\u0969\u096a\u096b\u096c for follow up.",
    ),
    (
        "unicode.context-term-after-devanagari-bank",
        "\u092c\u0948\u0902\u0915bank branch code TEMP0000123 for the refund.",
    ),
    (
        "unicode.context-term-after-devanagari-passport",
        "\u092a\u093e\u0938\u092a\u094b\u0930\u094d\u091fpassport M8231765 checked at immigration.",
    ),
    (
        "unicode.context-term-after-devanagari-abha",
        "\u0938\u094d\u0935\u093e\u0938\u094d\u0925\u094d\u092f"
        "abha 91234567890123 registered today.",
    ),
    (
        "unicode.context-term-after-devanagari-voter",
        "\u092e\u0924\u0926\u093e\u0928voter epic ABC1234567 verified.",
    ),
    (
        "unicode.context-term-after-devanagari-patient",
        "\u0905\u0938\u094d\u092a\u0924\u093e\u0932patient diagnosis E11 recorded at discharge.",
    ),
    (
        "unicode.astral-emoji-before-aadhaar",
        "\U0001f4ce Aadhaar 7534 7930 7460 issued at the enrolment camp.",
    ),
    (
        "unicode.astral-family-emoji-before-pan",
        "\U0001f468\u200d\U0001f469\u200d\U0001f467 PAN HKPPS5875Q filed with form 16.",
    ),
    (
        "unicode.astral-emoji-between-two-identifiers",
        "UHID 0024518 \U0001f4ce Aadhaar 7534 7930 7460 \U0001f9ea sample no LAB2024991.",
    ),
)

_FIXTURE_CONFIG: Final[DetectionConfig] = DetectionConfig(nlp=NlpConfig(use_ner=False))


def _utf16_offset(text: str, index: int | None) -> int | None:
    if index is None:
        return None
    return len(text[:index].encode("utf-16-le")) // 2


def _entity_record(entity: Entity, text: str) -> dict[str, Any]:
    return {
        "type": entity.type,
        "start": entity.start,
        "end": entity.end,
        "utf16Start": _utf16_offset(text, entity.start),
        "utf16End": _utf16_offset(text, entity.end),
        "maskedValue": entity.masked_value,
        "valueHash": entity.value_hash,
        "confidence": entity.confidence,
    }


def _case(case_id: str, text: str) -> dict[str, Any]:
    detected = analyze(text, "body", _FIXTURE_CONFIG)
    return {
        "id": case_id,
        "text": text,
        "entities": [_entity_record(entity, text) for entity in detected],
    }


def _corpus_texts() -> list[tuple[str, str]]:
    seen: set[str] = set()
    texts: list[tuple[str, str]] = []
    for sample in load_corpus(CORPUS_DIR):
        for field in ("subject", "body"):
            text = sample.subject if field == "subject" else sample.body
            if not text.strip() or text in seen:
                continue
            seen.add(text)
            texts.append((f"{sample.id}.{field}", text))
    return texts


def build_fixture() -> dict[str, Any]:
    cases = [_case(case_id, text) for case_id, text in _corpus_texts()]
    cases.extend(_case(case_id, text) for case_id, text in UNICODE_CASES)
    return {
        "version": RULE_PACK_VERSION,
        "serverOnlyRules": SERVER_ONLY_RULES,
        "browserValidatorDivergences": BROWSER_VALIDATOR_DIVERGENCES,
        "cases": cases,
    }


def render_fixture() -> str:
    return json.dumps(build_fixture(), indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blade_detection.evaluation.parity")
    parser.add_argument("--out", type=Path, default=FIXTURE_PATH)
    args = parser.parse_args(argv)
    payload = render_fixture()
    args.out.write_text(payload, encoding="utf-8")
    sys.stderr.write(f"parity fixture written to {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
