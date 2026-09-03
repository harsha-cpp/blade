from __future__ import annotations

import pytest

from blade_backend.policy import (
    BUILTIN_POLICY_SET,
    PolicyRule,
    PolicySet,
    RuleConditions,
    build_facts,
    evaluate,
)
from blade_backend.policy.models import RecipientClass, SenderClass, Severity


def _decide(
    *,
    entities: list[tuple[str, str]] | None = None,
    risk: float = 0.0,
    severity: Severity = "none",
    recipients: list[RecipientClass] | None = None,
    sender: SenderClass = "internal",
    attachments: bool = False,
    attachments_unscanned: int = 0,
    policy_set: PolicySet | None = None,
):
    facts = build_facts(
        entities=entities or [],
        risk_score=risk,
        severity=severity,
        recipient_classes=recipients if recipients is not None else ["internal"],
        sender_class=sender,
        has_attachments=attachments,
        attachments_unscanned=attachments_unscanned,
    )
    return evaluate(facts, policy_set)


def test_blocked_recipient_always_blocks() -> None:
    decision = _decide(recipients=["internal", "blocked"])
    assert decision.action == "block"
    assert decision.matched_policy_ids == ["blocked-recipient-domain"]


def test_unapproved_sender_with_phi_is_blocked() -> None:
    decision = _decide(
        entities=[("MRN", "***4518")],
        risk=52.5,
        severity="medium",
        recipients=["internal"],
        sender="public_email",
    )
    assert decision.action == "block"
    assert decision.matched_policy_ids == ["unapproved-sender-with-phi"]


def test_internal_sender_with_phi_to_internal_recipients_is_allowed() -> None:
    decision = _decide(
        entities=[("MRN", "***4518")],
        risk=52.5,
        severity="medium",
        recipients=["internal"],
        sender="internal",
    )
    assert decision.action == "allow"


def test_clean_message_is_allowed_silently() -> None:
    decision = _decide(recipients=["public_email"])
    assert decision.action == "allow"
    assert decision.user_message == ""


def test_phi_to_approved_partner_is_allowed_and_audited() -> None:
    decision = _decide(
        entities=[("IN_AADHAAR", "****7460")],
        risk=71.3,
        severity="high",
        recipients=["approved_partner", "internal"],
    )
    assert decision.action == "allow"
    assert decision.matched_policy_ids == ["approved-recipients-phi"]


def test_high_risk_phi_to_personal_gmail_is_quarantined() -> None:
    decision = _decide(
        entities=[("IN_AADHAAR", "****7460")],
        risk=71.3,
        severity="high",
        recipients=["public_email"],
    )
    assert decision.action == "quarantine"
    assert decision.severity == "high"


def test_discharge_summary_to_personal_gmail_is_not_merely_a_warning() -> None:
    decision = _decide(
        entities=[("MRN", "***4518"), ("ICD10", "E11.9"), ("PERSON", "Lakshmi Devi")],
        risk=80.1,
        severity="critical",
        recipients=["public_email"],
    )
    assert decision.action in {"quarantine", "block"}


def test_bulk_export_to_external_is_blocked_not_quarantined() -> None:
    entities = [("MRN", f"***{index:04d}") for index in range(6)]
    decision = _decide(
        entities=entities,
        risk=97.0,
        severity="critical",
        recipients=["external"],
    )
    assert decision.action == "block"
    assert decision.matched_policy_ids == ["bulk-export-external"]


def test_repeating_one_identifier_is_not_a_bulk_export() -> None:
    entities = [("MRN", "***4518")] * 6
    decision = _decide(
        entities=entities,
        risk=60.0,
        severity="high",
        recipients=["external"],
    )
    assert decision.matched_policy_ids != ["bulk-export-external"]


def test_moderate_risk_external_warns() -> None:
    decision = _decide(
        entities=[("ICD10", "E11.9")],
        risk=36.0,
        severity="medium",
        recipients=["external"],
    )
    assert decision.action == "warn"


def test_empty_recipient_list_does_not_satisfy_all_approved_by_vacuous_truth() -> None:
    decision = _decide(
        entities=[("IN_AADHAAR", "****7460")],
        risk=71.3,
        severity="high",
        recipients=[],
    )
    assert decision.matched_policy_ids != ["approved-recipients-phi"]


def test_first_matching_rule_wins_in_declared_order() -> None:
    policy_set = PolicySet(
        version="test",
        rules=[
            PolicyRule(
                id="later-block",
                order=20,
                conditions=RuleConditions(min_entity_count=1),
                action="block",
            ),
            PolicyRule(
                id="earlier-warn",
                order=10,
                conditions=RuleConditions(min_entity_count=1),
                action="warn",
            ),
        ],
    )
    decision = _decide(entities=[("MRN", "***1")], risk=50, policy_set=policy_set)
    assert decision.matched_policy_ids == ["earlier-warn"]


def test_disabled_rules_are_skipped() -> None:
    policy_set = PolicySet(
        version="test",
        rules=[
            PolicyRule(
                id="off",
                order=10,
                enabled=False,
                conditions=RuleConditions(min_entity_count=1),
                action="block",
            )
        ],
    )
    decision = _decide(entities=[("MRN", "***1")], risk=50, policy_set=policy_set)
    assert decision.matched_policy_ids == ["no-matching-rule"]
    assert decision.action == "warn"


def test_a_policy_set_with_no_matching_rule_warns_rather_than_allowing() -> None:
    empty = PolicySet(version="test", rules=[])
    decision = _decide(entities=[("IN_AADHAAR", "****7460")], risk=71.3, policy_set=empty)
    assert decision.action == "warn"


def test_min_reported_severity_raises_but_never_lowers() -> None:
    policy_set = PolicySet(
        version="test",
        rules=[
            PolicyRule(
                id="floor",
                order=10,
                conditions=RuleConditions(min_entity_count=1),
                action="warn",
                min_reported_severity="medium",
            )
        ],
    )
    raised = _decide(entities=[("MRN", "***1")], risk=20, severity="low", policy_set=policy_set)
    assert raised.severity == "medium"

    kept = _decide(entities=[("MRN", "***1")], risk=90, severity="critical", policy_set=policy_set)
    assert kept.severity == "critical"


@pytest.mark.parametrize("rule", BUILTIN_POLICY_SET.rules)
def test_every_builtin_rule_has_a_stable_id_and_message(rule: PolicyRule) -> None:
    assert rule.id
    if rule.action != "allow":
        assert rule.user_message, f"{rule.id} would show the user an empty explanation"


def test_builtin_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in BUILTIN_POLICY_SET.rules]
    assert len(ids) == len(set(ids))


def test_an_unreadable_attachment_never_settles_as_a_silent_allow() -> None:
    clean = _decide(attachments=True)
    assert clean.action == "allow"

    degraded = _decide(attachments=True, attachments_unscanned=1)
    assert degraded.action == "warn"
    assert degraded.matched_policy_ids == ["unscannable-attachment"]
    assert degraded.user_message == "One or more attachments could not be checked."
    assert degraded.severity == "medium"


def test_an_unreadable_attachment_never_weakens_a_stronger_verdict() -> None:
    quarantined = _decide(
        entities=[("IN_AADHAAR", "****7460")],
        risk=80,
        severity="high",
        recipients=["public_email"],
        attachments=True,
        attachments_unscanned=1,
    )
    assert quarantined.action == "quarantine"
    assert quarantined.matched_policy_ids == ["high-risk-phi-to-public-email"]


def test_a_stored_policy_without_the_new_condition_still_evaluates() -> None:
    stored = PolicySet.model_validate(
        {
            "version": "ward-policy-v3",
            "rules": [
                {
                    "id": "allow-everything",
                    "order": 10,
                    "conditions": {"max_risk_score": 100},
                    "action": "allow",
                    "user_message": "",
                }
            ],
        }
    )

    assert stored.rules[0].conditions.min_unscanned_attachments is None

    clean = _decide(policy_set=stored)
    assert clean.action == "allow"
    assert clean.matched_policy_ids == ["allow-everything"]

    degraded = _decide(policy_set=stored, attachments=True, attachments_unscanned=1)
    assert degraded.action == "warn"
    assert degraded.matched_policy_ids == ["unscannable-attachment"]


def test_an_organization_can_hold_unreadable_attachments_for_review() -> None:
    strict = PolicySet(
        version="strict",
        rules=[
            PolicyRule(
                id="hold-unreadable-attachments",
                order=10,
                conditions=RuleConditions(min_unscanned_attachments=1),
                action="quarantine",
                min_reported_severity="high",
                user_message="An unreadable attachment is held for review.",
            ),
            PolicyRule(
                id="allow-everything",
                order=20,
                conditions=RuleConditions(max_risk_score=100),
                action="allow",
            ),
        ],
    )

    decision = _decide(policy_set=strict, attachments=True, attachments_unscanned=1)
    assert decision.action == "quarantine"
    assert decision.matched_policy_ids == ["hold-unreadable-attachments"]
