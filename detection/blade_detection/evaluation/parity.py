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
)

_FIXTURE_CONFIG: Final[DetectionConfig] = DetectionConfig(nlp=NlpConfig(use_ner=False))


def _entity_record(entity: Entity) -> dict[str, Any]:
    return {
        "type": entity.type,
        "start": entity.start,
        "end": entity.end,
        "maskedValue": entity.masked_value,
    }


def _case(case_id: str, text: str) -> dict[str, Any]:
    detected = analyze(text, "body", _FIXTURE_CONFIG)
    return {
        "id": case_id,
        "text": text,
        "entities": [_entity_record(entity) for entity in detected],
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
