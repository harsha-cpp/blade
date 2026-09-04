from __future__ import annotations

from dataclasses import replace

from blade_detection.config import DetectionConfig
from blade_detection.masking import mask_value
from blade_detection.models import Entity, EntitySource
from blade_detection.normalization import FoldedText, fold, value_fingerprint
from blade_detection.recognition.ner import (
    NER_CONFIDENCE,
    NER_PRIORITY,
    NlpModelUnavailableError,
    person_spans,
)
from blade_detection.recognition.pattern_safety import is_safe_pattern
from blade_detection.recognition.patterns import RawMatch, apply_rules
from blade_detection.recognition.resolution import resolve_overlaps
from blade_detection.rules import BUILTIN_RULE_PACK
from blade_detection.rules.schema import Rule, RulePack

__all__ = [
    "NlpModelUnavailableError",
    "RawMatch",
    "analyze",
    "custom_mrn_rules",
]


def _enabled_types(config: DetectionConfig) -> frozenset[str] | None:
    toggles = config.recognizers
    disabled: set[str] = set()
    if not toggles.enable_aadhaar:
        disabled.add("IN_AADHAAR")
    if not toggles.enable_pan:
        disabled.add("IN_PAN")
    if not toggles.enable_abha:
        disabled.update({"ABHA_NUMBER", "ABHA_ADDRESS"})
    if not toggles.enable_mrn:
        disabled.update({"MRN", "PATIENT_VISIT_ID"})
    if not toggles.enable_icd10:
        disabled.add("ICD10")
    if not disabled:
        return None
    return frozenset(
        rule.entity_type for rule in BUILTIN_RULE_PACK.rules if rule.entity_type not in disabled
    )


def custom_mrn_rules(config: DetectionConfig) -> list[Rule]:
    return [
        Rule(
            entity_type="MRN",
            name=f"mrn-tenant-{index}",
            pattern=pattern,
            base_confidence=0.75,
            priority=55,
        )
        for index, pattern in enumerate(config.recognizers.custom_mrn_patterns, start=1)
        if is_safe_pattern(pattern)
    ]


def _in_source_text(match: RawMatch, folded: FoldedText) -> RawMatch:
    if folded.offsets is None:
        return match
    start, end = folded.original_span(match.start, match.end)
    return replace(match, value=folded.source[start:end], start=start, end=end)


def analyze(
    text: str,
    source: EntitySource,
    config: DetectionConfig,
    *,
    attachment_id: str | None = None,
    rule_pack: RulePack | None = None,
) -> list[Entity]:
    if not text.strip():
        return []

    pack = rule_pack or BUILTIN_RULE_PACK
    rules = pack.for_types(_enabled_types(config)) + custom_mrn_rules(config)
    folded = fold(text)
    matches = [_in_source_text(match, folded) for match in apply_rules(rules, folded.text)]

    if config.nlp.use_ner:
        matches.extend(
            RawMatch(
                entity_type="PERSON",
                rule_name="spacy-ner",
                value=span.text,
                start=span.start,
                end=span.end,
                confidence=NER_CONFIDENCE,
                priority=NER_PRIORITY,
                had_context=False,
            )
            for span in person_spans(text, config.nlp.spacy_model)
        )

    return [
        Entity(
            type=match.entity_type,
            masked_value=mask_value(match.value, match.entity_type),
            value_hash=value_fingerprint(match.entity_type, match.value),
            confidence=match.confidence,
            source=source,
            attachment_id=attachment_id,
            start=match.start,
            end=match.end,
        )
        for match in resolve_overlaps(matches)
    ]
