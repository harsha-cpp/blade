from __future__ import annotations

from typing import Final

CANONICAL_TYPES: Final[frozenset[str]] = frozenset(
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
        "LAB_ACCESSION",
        "ICD10",
        "MEDICAL_LICENSE",
        "INSURANCE_POLICY",
        "BANK_ACCOUNT",
        "IN_IFSC",
        "IN_UPI",
        "IN_GSTIN",
        "PERSON",
        "DATE_OF_BIRTH",
        "IN_PHONE",
        "EMAIL_ADDRESS",
    }
)
