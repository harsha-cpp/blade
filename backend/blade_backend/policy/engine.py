from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from blade_backend.policy.defaults import (
    BUILTIN_POLICY_SET,
    UNSCANNED_ATTACHMENT_RULE,
)
from blade_backend.policy.models import (
    ACTION_RANK,
    SEVERITY_RANK,
    Action,
    PolicyRule,
    PolicySet,
    RecipientClass,
    SenderClass,
    Severity,
)

SUBJECT_IDENTIFIERS = frozenset(
    {"MRN", "PATIENT_VISIT_ID", "ABHA_NUMBER", "ABHA_ADDRESS", "IN_AADHAAR", "LAB_ACCESSION"}
)

APPROVED_RECIPIENT_CLASSES = frozenset({"internal", "approved_partner"})


def _empty_strings() -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class ScanFacts:
    entity_types: frozenset[str]
    distinct_entity_count: int
    distinct_subject_count: int
    risk_score: float
    severity: Severity
    recipient_classes: tuple[RecipientClass, ...]
    sender_class: SenderClass
    has_attachments: bool
    attachments_unscanned: int = 0


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: Action
    severity: Severity
    risk_score: float
    matched_policy_ids: list[str] = field(default_factory=_empty_strings)
    user_message: str = ""


def build_facts(
    *,
    entities: list[tuple[str, str]],
    risk_score: float,
    severity: Severity,
    recipient_classes: list[RecipientClass],
    sender_class: SenderClass,
    has_attachments: bool,
    attachments_unscanned: int = 0,
) -> ScanFacts:
    distinct = {(entity_type, value) for entity_type, value in entities}
    subjects = {
        (entity_type, value)
        for entity_type, value in distinct
        if entity_type in SUBJECT_IDENTIFIERS
    }
    return ScanFacts(
        entity_types=frozenset(entity_type for entity_type, _ in distinct),
        distinct_entity_count=len(distinct),
        distinct_subject_count=len(subjects),
        risk_score=risk_score,
        severity=severity,
        recipient_classes=tuple(recipient_classes),
        sender_class=sender_class,
        has_attachments=has_attachments,
        attachments_unscanned=attachments_unscanned,
    )


def _matches(rule: PolicyRule, facts: ScanFacts) -> bool:  # noqa: PLR0911, PLR0912
    conditions = rule.conditions

    if conditions.entity_types_any and not (facts.entity_types & set(conditions.entity_types_any)):
        return False
    if conditions.entity_types_all and not (set(conditions.entity_types_all) <= facts.entity_types):
        return False
    if (
        conditions.min_entity_count is not None
        and facts.distinct_entity_count < conditions.min_entity_count
    ):
        return False
    if (
        conditions.min_subject_count is not None
        and facts.distinct_subject_count < conditions.min_subject_count
    ):
        return False
    if (
        conditions.min_unscanned_attachments is not None
        and facts.attachments_unscanned < conditions.min_unscanned_attachments
    ):
        return False
    if conditions.min_risk_score is not None and facts.risk_score < conditions.min_risk_score:
        return False
    if conditions.max_risk_score is not None and facts.risk_score > conditions.max_risk_score:
        return False
    if conditions.min_severity is not None and (
        SEVERITY_RANK[facts.severity] < SEVERITY_RANK[conditions.min_severity]
    ):
        return False
    if conditions.recipient_class_any and not (
        set(facts.recipient_classes) & set(conditions.recipient_class_any)
    ):
        return False
    if conditions.recipient_class_all:
        if not facts.recipient_classes:
            return False
        if not set(facts.recipient_classes) <= set(conditions.recipient_class_all):
            return False
    if conditions.sender_class_any and facts.sender_class not in set(conditions.sender_class_any):
        return False
    return not (
        conditions.has_attachments is not None
        and facts.has_attachments != conditions.has_attachments
    )


def _raise_severity(current: Severity, floor: Severity | None) -> Severity:
    if floor is None:
        return current
    return current if SEVERITY_RANK[current] >= SEVERITY_RANK[floor] else floor


def evaluate(facts: ScanFacts, policy_set: PolicySet | None = None) -> PolicyDecision:
    resolved = policy_set or BUILTIN_POLICY_SET
    decision = _first_match(facts, resolved)
    if facts.attachments_unscanned and decision.action == "allow":
        return _unscanned_attachment_decision(facts, resolved)
    return decision


def _first_match(facts: ScanFacts, resolved: PolicySet) -> PolicyDecision:
    for rule in resolved.ordered():
        if not _matches(rule, facts):
            continue
        return PolicyDecision(
            action=rule.action,
            severity=_raise_severity(facts.severity, rule.min_reported_severity),
            risk_score=facts.risk_score,
            matched_policy_ids=[rule.id],
            user_message=rule.user_message,
        )

    return PolicyDecision(
        action="warn" if facts.distinct_entity_count else "allow",
        severity=facts.severity,
        risk_score=facts.risk_score,
        matched_policy_ids=["no-matching-rule"],
        user_message=(
            "No policy rule matched this message. Review before sending."
            if facts.distinct_entity_count
            else ""
        ),
    )


def _unscanned_attachment_decision(facts: ScanFacts, resolved: PolicySet) -> PolicyDecision:
    rule = next(
        (
            candidate
            for candidate in resolved.ordered()
            if candidate.conditions.min_unscanned_attachments is not None
            and _matches(candidate, facts)
        ),
        UNSCANNED_ATTACHMENT_RULE,
    )
    return PolicyDecision(
        action=strongest([rule.action, UNSCANNED_ATTACHMENT_RULE.action]),
        severity=_raise_severity(facts.severity, rule.min_reported_severity),
        risk_score=facts.risk_score,
        matched_policy_ids=[rule.id],
        user_message=rule.user_message or UNSCANNED_ATTACHMENT_RULE.user_message,
    )


ActionName = Literal["allow", "warn", "block", "quarantine", "escalate"]


def strongest(actions: list[ActionName]) -> ActionName:
    return max(actions, key=lambda action: ACTION_RANK[action]) if actions else "allow"
