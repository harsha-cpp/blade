from __future__ import annotations

import pytest

from blade_detection.api import detect_email
from blade_detection.models import EmailPayload
from blade_detection.recognition.patterns import MIN_CONFIDENCE
from blade_detection.recognition.validators import (
    CHECKSUM_VALIDATORS,
    validate_aadhaar,
    validate_driving_license,
    validate_gstin,
    validate_icd10,
    validate_ifsc,
    validate_ifsc_known_bank,
    validate_pan,
    validate_passport,
)
from blade_detection.rules import BUILTIN_RULES


def _types(body: str, subject: str = "") -> list[str]:
    result = detect_email(EmailPayload(subject=subject, body=body))
    return [entity.type for entity in result.entities]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7534 7930 7460", True),
        ("753479307460", True),
        ("7534-7930-7460", True),
        ("2345 6789 0123", False),
        ("1234 5678 9012", False),
        ("1111 1111 1111", False),
        ("75347930746", False),
    ],
)
def test_validate_aadhaar(value: str, expected: bool) -> None:
    assert validate_aadhaar(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HKPPS5875Q", True),
        ("ABCDE1234F", False),
        ("HKPZS5875Q", False),
        ("-7236-8829", False),
        ("AAAAA1234A", False),
    ],
)
def test_validate_pan(value: str, expected: bool) -> None:
    assert validate_pan(value) is expected


def test_validate_gstin_checks_the_check_digit() -> None:
    assert validate_gstin("29ILVPC5151I1ZI") is True
    assert validate_gstin("29ILVPC5151I1ZP") is False


def test_validate_ifsc() -> None:
    assert validate_ifsc("NBEF0A9M3FI") is True
    assert validate_ifsc("NBEF1A9M3FI") is False


def test_validate_passport_and_licence() -> None:
    assert validate_passport("M8234567") is True
    assert validate_passport("Q8234567") is False
    assert validate_driving_license("MH1220110012345") is True
    assert validate_driving_license("ZZ1220110012345") is False


def test_raw_fourteen_digits_need_abha_vocabulary() -> None:
    assert "ABHA_NUMBER" not in _types("Please process vendor invoice 12345678901234.")
    assert "ABHA_NUMBER" in _types("The health ID captured at the camp was 45120099887766.")


def test_prefixed_id_needs_clinical_context() -> None:
    assert "MRN" not in _types("Order HSP-2026-0012 for stationery has been delivered.")
    assert "MRN" in _types("Patient record SMH-2026-004417 admitted for treatment.")


def test_bare_icd_category_needs_clinical_context() -> None:
    assert "ICD10" not in _types("Known issue K21 in the tracker is deferred to v2.15.")
    assert "ICD10" in _types("Primary diagnosis K21 recorded for the admitted patient.")


def test_specific_icd_code_stands_without_a_diagnosis_keyword() -> None:
    assert "ICD10" in _types("Known hyperlipidaemia (E78.5) noted on the clinical review.")


def test_ten_digit_amounts_are_not_phone_numbers() -> None:
    assert "IN_PHONE" not in _types(
        "Capital budget 9845000000 rupees against a revised estimate of 8420000000."
    )
    assert "IN_PHONE" in _types("The patient's mobile number is 9845123456.")


def test_ordinary_prose_after_policy_is_not_a_policy_number() -> None:
    assert "INSURANCE_POLICY" not in _types(
        "The consent policy has been revised to require a witness signature."
    )
    assert "INSURANCE_POLICY" in _types("Policy number: P/181234/12/2026/004567")


def test_gstin_beats_the_pan_embedded_inside_it() -> None:
    types = _types("The employer's GSTIN is 29ILVPC5151I1ZI for billing.")
    assert "IN_GSTIN" in types
    assert "IN_PAN" not in types


def test_upi_handle_beats_email_and_phone() -> None:
    types = _types("Patient paid the deposit from 9845123456@ybl this morning.")
    assert "IN_UPI" in types
    assert "EMAIL_ADDRESS" not in types


def test_abha_address_beats_email() -> None:
    types = _types("Health locker address is ramesh.iyer@abdm for the upload.")
    assert "ABHA_ADDRESS" in types
    assert "EMAIL_ADDRESS" not in types


def test_comma_separated_identifier_lists_are_all_captured() -> None:
    result = detect_email(EmailPayload(body="UHIDs 0038001, 0038007 and 0038014 need follow up."))
    mrns = [entity for entity in result.entities if entity.type == "MRN"]
    assert len(mrns) == 3


def test_lowercase_prose_is_not_a_person() -> None:
    assert "PERSON" not in _types("the patient in bed 7 became oliguric overnight")


def test_titled_and_labelled_names_are_detected() -> None:
    assert "PERSON" in _types("Please admit Mrs Kalpana Venkataraman today.")
    assert "PERSON" in _types("Patient: Meera Sundaram, 62F, for review.")


def test_who_icd10_codes_used_in_india_are_accepted() -> None:
    assert validate_icd10("I64") is True
    assert validate_icd10("E14") is True
    assert validate_icd10("E14.9") is True
    assert validate_icd10("E11.9") is True


def test_junk_icd10_codes_are_still_rejected() -> None:
    assert validate_icd10("E99.999") is False
    assert validate_icd10("D91.0") is False
    assert validate_icd10("J59.1") is False
    assert validate_icd10("E11.") is False
    assert validate_icd10("E11.ABCDE") is False


def test_a_clinical_modification_subcode_is_validated_in_full() -> None:
    assert validate_icd10("E11.9") is True
    assert validate_icd10("S72.001A") is True
    assert validate_icd10("E11.99") is False
    assert validate_icd10("E11.9ZZZ") is False


def test_who_supplement_categories_keep_the_category_only_check() -> None:
    assert validate_icd10("I64") is True
    assert validate_icd10("E14") is True
    assert validate_icd10("E14.9") is True


def test_who_only_codes_are_detected_in_clinical_prose() -> None:
    assert "ICD10" in _types("Stroke coded as I64 for the admitted patient.")
    assert "ICD10" in _types("Discharge diagnosis E14.9 recorded for the patient.")
    assert "ICD10" not in _types("Known issue E99.999 in the tracker is deferred to v2.15.")


def test_idbi_bank_ifsc_is_recognised() -> None:
    assert "IN_IFSC" in _types("credit IBKL0001234")


def test_a_checksum_valid_aadhaar_survives_a_weak_negative_term() -> None:
    assert "IN_AADHAAR" in _types("Sharing the number 7534 7930 7460 for your reference.")


def test_a_verhoeff_valid_commercial_number_is_not_read_as_an_aadhaar() -> None:
    assert "IN_AADHAAR" not in _types("Invoice no 812806192057 raised against the purchase order")
    assert "IN_AADHAAR" not in _types(
        "Vendor transaction id 748233725236 was settled against the GST invoice."
    )
    assert "IN_AADHAAR" not in _types("Transaction 7534 7930 7460 was logged against the invoice.")


def test_checksum_backed_rules_cannot_be_vetoed_by_weak_vocabulary_alone() -> None:
    for rule in BUILTIN_RULES:
        if rule.validator not in CHECKSUM_VALIDATORS or not rule.weak_negative_terms:
            continue
        assert rule.base_confidence - rule.weak_negative_penalty >= MIN_CONFIDENCE, (
            f"{rule.name} can be vetoed by weak vocabulary despite a checksum validator"
        )


def test_a_strong_commercial_term_vetoes_a_checksum_backed_rule() -> None:
    for rule in BUILTIN_RULES:
        if rule.validator not in CHECKSUM_VALIDATORS or not rule.weak_negative_terms:
            continue
        assert rule.base_confidence - rule.negative_penalty < MIN_CONFIDENCE, (
            f"{rule.name} still passes with a strong commercial term in the window"
        )


def test_a_product_version_is_not_a_date_of_birth() -> None:
    assert "DATE_OF_BIRTH" not in _types("Install Adobe Reader 11/2/2024 on the workstation.")
    assert "DATE_OF_BIRTH" in _types("DOB 11/2/2024 was captured at admission.")


def test_an_unlisted_ifsc_prefix_needs_banking_context() -> None:
    assert "IN_IFSC" not in _types("The template code TEMP0000123 was rejected by the form.")
    assert "IN_IFSC" in _types("Credit the refund to IFSC TEMP0000123 this week.")


def test_a_listed_bank_prefix_is_detected_without_banking_vocabulary() -> None:
    assert "IN_IFSC" in _types("Please quote HDFC0001234 on the form.")
    assert validate_ifsc_known_bank("HDFC0001234") is True
    assert validate_ifsc_known_bank("TEMP0000123") is False


DEVANAGARI_AADHAAR = "\u096d\u096b\u0969\u096a \u096d\u096f\u0969\u0966 \u096d\u096a\u096c\u0966"
DEVANAGARI_AADHAAR_LABEL = "\u0906\u0927\u093e\u0930"
ZERO_WIDTH_JOINER = "\u200d"


def test_identifier_boundaries_do_not_depend_on_the_surrounding_script() -> None:
    assert "IN_AADHAAR" in _types(f"{DEVANAGARI_AADHAAR_LABEL} 7534 7930 7460 issued.")
    assert "IN_AADHAAR" in _types(f"Aadhaar {DEVANAGARI_AADHAAR} issued.")


def test_zero_width_characters_do_not_split_an_identifier() -> None:
    assert "IN_PAN" in _types(f"PAN HKP{ZERO_WIDTH_JOINER}PS5875Q filed with form 16.")


INVISIBLE_SPLITTERS = (
    "\u00ad",
    "\u034f",
    "\u180e",
    "\u2060",
    "\u2061",
    "\u2064",
    "\ufe00",
    "\ufe0f",
)


@pytest.mark.parametrize("invisible", INVISIBLE_SPLITTERS)
def test_every_folded_invisible_character_keeps_an_identifier_whole(invisible: str) -> None:
    assert "IN_PAN" in _types(f"PAN HKP{invisible}PS5875Q filed with form 16.")
    assert "IN_AADHAAR" in _types(f"Aadhaar 7534 79{invisible}30 7460 issued.")


def test_offsets_and_masked_values_refer_to_the_original_text() -> None:
    body = f"Aadhaar {DEVANAGARI_AADHAAR} issued."
    result = detect_email(EmailPayload(body=body))
    aadhaar = [entity for entity in result.entities if entity.type == "IN_AADHAAR"]
    assert len(aadhaar) == 1
    entity = aadhaar[0]
    assert entity.start is not None and entity.end is not None
    assert body[entity.start : entity.end] == DEVANAGARI_AADHAAR
    assert entity.masked_value.endswith(DEVANAGARI_AADHAAR[-4:])


def test_institutional_phrases_are_not_people() -> None:
    assert "PERSON" not in _types("Department of Internal Medicine for Clinical Excellence")
    assert "PERSON" not in _types("Raise a purchase order for Medical Sciences Board today.")
    assert "PERSON" in _types("Reports are ready for Ramesh Kumar Iyer as discussed.")
