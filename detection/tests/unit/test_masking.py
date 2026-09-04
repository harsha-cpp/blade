from __future__ import annotations

import pytest

from blade_detection.api import detect_email
from blade_detection.masking import mask_value
from blade_detection.models import EmailPayload, Entity
from blade_detection.normalization import value_fingerprint
from blade_detection.scoring import total_weight


def test_untyped_masking_keeps_the_legacy_shape() -> None:
    assert mask_value("753479307460") == "********7460"
    assert mask_value("abc") == "***"
    assert mask_value("   ") == ""


@pytest.mark.parametrize(
    ("entity_type", "value", "expected"),
    [
        ("ICD10", "E11.9", "*****"),
        ("DATE_OF_BIRTH", "14/07/1976", "**********"),
        ("PERSON", "Ramesh Kumar Iyer", "*****************"),
        ("EMAIL_ADDRESS", "ramesh.iyer88@apollo.in", "*************@apollo.in"),
        ("MRN", "0024518", "*****18"),
        ("IN_AADHAAR", "7534 7930 7460", "**********7460"),
        ("BANK_ACCOUNT", "50100234567891", "**********7891"),
    ],
)
def test_type_aware_masking(entity_type: str, value: str, expected: str) -> None:
    assert mask_value(value, entity_type) == expected


def test_email_without_a_domain_is_fully_masked() -> None:
    assert mask_value("@apollo.in", "EMAIL_ADDRESS") == "**********"
    assert mask_value("ramesh@", "EMAIL_ADDRESS") == "*******"


def test_a_short_identifier_keeps_less_than_four_characters() -> None:
    assert mask_value("0024518", "MRN") == "*****18"
    assert mask_value("012345", "MRN") == "****45"
    assert mask_value("0123", "MRN") == "***3"
    assert mask_value("01", "MRN") == "**"


def _entity(entity_type: str, masked: str, value: str) -> Entity:
    return Entity(
        type=entity_type,
        masked_value=masked,
        value_hash=value_fingerprint(entity_type, value),
        confidence=1.0,
        source="body",
    )


def test_two_aadhaars_sharing_their_last_four_digits_count_separately() -> None:
    same_tail = [
        _entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
        _entity("IN_AADHAAR", "**********7460", "2846 1053 7460"),
    ]
    repeated = [
        _entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
        _entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
    ]
    assert total_weight(same_tail) > total_weight(repeated)


def test_two_people_of_the_same_length_count_separately() -> None:
    distinct = [
        _entity("PERSON", "************", "Ramesh Kumar"),
        _entity("PERSON", "************", "Sunita Bhatt"),
    ]
    repeated = [
        _entity("PERSON", "************", "Ramesh Kumar"),
        _entity("PERSON", "************", "Ramesh Kumar"),
    ]
    assert total_weight(distinct) > total_weight(repeated)


def test_entities_without_a_hash_still_group_on_the_masked_value() -> None:
    entities = [
        Entity(type="MRN", masked_value="*****18", confidence=1.0, source="body"),
        Entity(type="MRN", masked_value="*****18", confidence=1.0, source="body"),
    ]
    single = [Entity(type="MRN", masked_value="*****18", confidence=1.0, source="body")]
    assert total_weight(entities) > total_weight(single)
    assert total_weight(entities) < 2 * total_weight(single)


def test_a_detected_person_is_fully_masked_end_to_end() -> None:
    result = detect_email(EmailPayload(body="Please admit Mrs Kalpana Venkataraman today."))
    people = [entity for entity in result.entities if entity.type == "PERSON"]
    assert people
    assert all(set(entity.masked_value) == {"*"} for entity in people)
    assert all(entity.value_hash for entity in people)
