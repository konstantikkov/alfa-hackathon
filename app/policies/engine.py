from __future__ import annotations

from app.config.consumers import ConsumerConfig
from app.core.enums import Decision
from app.core.models import Candidate, PrivacyDecision
from app.policies.relationship import (
    DEFAULT_RELATIONSHIP_RULES,
    RelationshipRule,
    is_nearby,
)


class PolicyEngine:
    """Pure function: (consumer policy, contextual decisions) -> final decisions.

    Two things happen here, and nowhere else:
    1. Relationship rules -- multi-entity predicates that override a single-candidate
       decision (e.g. downgrade a lone PIN to KEEP when no card number is nearby).
    2. Consumer policy -- a type this consumer didn't ask to have masked is forced to
       KEEP, regardless of how sensitive the privacy engine thinks it is.
    """

    def __init__(
        self,
        relationship_rules: tuple[RelationshipRule, ...] = DEFAULT_RELATIONSHIP_RULES,
    ) -> None:
        self._rules = relationship_rules

    def evaluate(
        self,
        text: str,
        decisions: list[PrivacyDecision],
        all_candidates: list[Candidate],
        consumer: ConsumerConfig,
    ) -> list[PrivacyDecision]:
        result = [self._apply_relationship_rules(text, d, all_candidates) for d in decisions]
        result = [self._apply_consumer_policy(d, consumer) for d in result]
        return result

    def _apply_relationship_rules(
        self, text: str, decision: PrivacyDecision, all_candidates: list[Candidate]
    ) -> PrivacyDecision:
        if decision.decision != Decision.MASK:
            return decision

        for rule in self._rules:
            if decision.candidate.type != rule.when_type:
                continue
            satisfied = any(
                other.type == rule.requires_nearby_type
                and is_nearby(
                    rule,
                    text,
                    decision.candidate.start,
                    decision.candidate.end,
                    other.start,
                    other.end,
                )
                for other in all_candidates
                if other is not decision.candidate
            )
            if not satisfied:
                return PrivacyDecision(
                    candidate=decision.candidate,
                    decision=Decision.KEEP,
                    confidence=0.6,
                    reasons=(
                        *decision.reasons,
                        f"relationship_rule_unmet:{rule.requires_nearby_type.value}",
                    ),
                )
        return decision

    def _apply_consumer_policy(self, decision: PrivacyDecision, consumer: ConsumerConfig) -> PrivacyDecision:
        if decision.decision == Decision.MASK and not consumer.should_mask_type(decision.candidate.type):
            return PrivacyDecision(
                candidate=decision.candidate,
                decision=Decision.KEEP,
                confidence=decision.confidence,
                reasons=(*decision.reasons, "consumer_policy_excludes_type"),
            )
        return decision
