from __future__ import annotations

from blade_detection.models import Entity, Severity
from blade_detection.normalization import normalise_value
from blade_detection.scoring.weights import (
    SENSITIVITY_WEIGHTS,
    bucket,
    repeat_factor,
    risk_from_weight,
    weight_for,
)

DIRECT_IDENTIFIERS = frozenset(
    {
        "IN_AADHAAR",
        "IN_PAN",
        "IN_PASSPORT",
        "IN_DRIVING_LICENSE",
        "IN_VOTER_ID",
        "ABHA_NUMBER",
        "ABHA_ADDRESS",
        "MRN",
        "PATIENT_VISIT_ID",
        "BANK_ACCOUNT",
    }
)

CLINICAL_TYPES = frozenset({"ICD10", "LAB_ACCESSION", "MEDICAL_LICENSE"})

COMBINATION_MULTIPLIER = 1.2


def _group_key(entity: Entity) -> str:
    if entity.value_hash:
        return entity.value_hash
    return normalise_value(entity.type, entity.masked_value)


def total_weight(entities: list[Entity]) -> float:
    grouped: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        grouped.setdefault((entity.type, _group_key(entity)), []).append(entity)

    weight = 0.0
    for (entity_type, _value), group in grouped.items():
        best_confidence = max(item.confidence for item in group)
        weight += weight_for(entity_type) * best_confidence * repeat_factor(len(group))

    present = {entity.type for entity in entities}
    if present & DIRECT_IDENTIFIERS and present & CLINICAL_TYPES:
        weight *= COMBINATION_MULTIPLIER
    return weight


def score(entities: list[Entity]) -> tuple[float, Severity]:
    if not entities:
        return 0.0, "none"
    risk = risk_from_weight(total_weight(entities))
    return risk, bucket(risk)


__all__ = ["SENSITIVITY_WEIGHTS", "bucket", "score", "total_weight"]
