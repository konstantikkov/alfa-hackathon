from __future__ import annotations

from app.context.rules import (
    BANK_PRIVATE_CONCEPTS,
    ORGANIZATIONAL_ADDRESS_MARKERS,
    PERSONAL_ADDRESS_MARKERS,
    PERSONAL_ROLES,
    PRIVATE_ACTIONS,
    PUBLIC_MARKERS,
    STRONG_RELATED_PII_KEYWORDS,
    attributed_window,
    count_hits,
)
from app.core.enums import Decision, PIIType
from app.core.interfaces import PrivacyDecider
from app.core.models import Candidate, PrivacyDecision


class ContextualPrivacyEngine(PrivacyDecider):
    """Answers "is *this* occurrence personal data?", not "is this a PERSON?".

    Most structured types (email, phone, INN, card, passport, ...) are personal by
    construction once a detector's format+checksum+keyword requirements are met -- the
    detector itself already encodes the context requirement (e.g. DEPARTMENT_CODE only
    fires near a passport-issuance keyword). FULL_NAME and ADDRESS are the two types
    that genuinely need scoring: a name/address span alone is structurally ambiguous.
    """

    def __init__(self, context_window_chars: int = 220, emit_uncertain: bool = False) -> None:
        self._window_chars = context_window_chars
        # When an ML fallback sits behind this engine, no-signal cases become
        # Decision.UNCERTAIN instead of the fail-safe MASK; without ML the
        # fail-safe stands (KEEPing a real person's name is the worse error).
        self._emit_uncertain = emit_uncertain

    def decide(self, text: str, candidate: Candidate, all_candidates: list[Candidate]) -> PrivacyDecision:
        other_spans = [(c.start, c.end) for c in all_candidates if c is not candidate]
        window = attributed_window(text, candidate.start, candidate.end, other_spans, self._window_chars)
        window_lower = window.lower()

        if candidate.type == PIIType.FULL_NAME:
            return self._decide_full_name(candidate, window_lower)
        if candidate.type == PIIType.ADDRESS:
            return self._decide_address(candidate, window_lower)

        return PrivacyDecision(
            candidate=candidate,
            decision=Decision.MASK,
            confidence=candidate.detector_confidence,
            reasons=("personal_by_construction",),
        )

    def _decide_full_name(self, candidate: Candidate, window_lower: str) -> PrivacyDecision:
        role_hits = count_hits(window_lower, PERSONAL_ROLES)
        action_hits = count_hits(window_lower, PRIVATE_ACTIONS)
        bank_hits = count_hits(window_lower, BANK_PRIVATE_CONCEPTS)
        strong_pii_hits = count_hits(window_lower, STRONG_RELATED_PII_KEYWORDS)
        public_hits = count_hits(window_lower, PUBLIC_MARKERS)
        is_known_public = bool(candidate.metadata.get("known_public_figure"))

        private_score = len(role_hits) * 2 + len(action_hits) * 2 + len(bank_hits) + len(strong_pii_hits) * 3
        public_score = len(public_hits) * 2 + (2 if is_known_public else 0)

        reasons: list[str] = []
        reasons += [f"role:{h}" for h in role_hits]
        reasons += [f"action:{h}" for h in action_hits]
        reasons += [f"bank_concept:{h}" for h in bank_hits]
        reasons += [f"strong_pii:{h}" for h in strong_pii_hits]
        reasons += [f"public_marker:{h}" for h in public_hits]
        if is_known_public:
            reasons.append("known_public_figure")

        candidate.metadata["rule_scores"] = {
            "private": private_score,
            "public": public_score,
        }

        # Strong private context always overrides a public-figure hint (spec section 8):
        # "Поэт Александр Пушкин, паспорт ..., хочет взять кредит" must still be MASK.
        if private_score > 0:
            confidence = min(0.6 + 0.1 * private_score, 0.99)
            return PrivacyDecision(
                candidate,
                Decision.MASK,
                confidence,
                tuple(reasons) or ("private_context",),
            )

        if public_score > 0:
            confidence = min(0.6 + 0.1 * public_score, 0.95)
            return PrivacyDecision(
                candidate,
                Decision.KEEP,
                confidence,
                tuple(reasons) or ("public_context",),
            )

        # No signal either way: fail-safe toward protecting a real person's name,
        # or hand the ambiguity to the ML fallback when one is configured.
        if self._emit_uncertain:
            return PrivacyDecision(candidate, Decision.UNCERTAIN, 0.5, ("no_context_uncertain",))
        return PrivacyDecision(candidate, Decision.MASK, 0.55, ("no_context_fail_safe",))

    def _decide_address(self, candidate: Candidate, window_lower: str) -> PrivacyDecision:
        value_lower = candidate.value.lower()
        org_hits = count_hits(window_lower, ORGANIZATIONAL_ADDRESS_MARKERS) + count_hits(
            value_lower, ORGANIZATIONAL_ADDRESS_MARKERS
        )
        personal_hits = count_hits(window_lower, PERSONAL_ADDRESS_MARKERS)

        candidate.metadata["rule_scores"] = {
            "private": len(personal_hits),
            "public": len(org_hits),
        }

        if personal_hits:
            confidence = min(0.6 + 0.1 * len(personal_hits), 0.97)
            return PrivacyDecision(
                candidate,
                Decision.MASK,
                confidence,
                tuple(f"personal:{h}" for h in personal_hits),
            )
        if org_hits:
            confidence = min(0.6 + 0.1 * len(org_hits), 0.95)
            return PrivacyDecision(
                candidate,
                Decision.KEEP,
                confidence,
                tuple(f"organizational:{h}" for h in org_hits),
            )

        if self._emit_uncertain:
            return PrivacyDecision(candidate, Decision.UNCERTAIN, 0.5, ("no_context_uncertain",))
        return PrivacyDecision(candidate, Decision.MASK, 0.55, ("no_context_fail_safe",))
