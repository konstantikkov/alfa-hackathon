from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import PIIType


@dataclass(frozen=True)
class RelationshipRule:
    """Declarative predicate: "TYPE only stays MASK if TYPE2 is nearby".

    Centralizes what would otherwise be scattered `if` statements (spec section 9).
    Example: a bare PIN is usually a confirmation/order code, not card data -- it only
    becomes personal once a card number is mentioned nearby (spec + hackathon bonus:
    "пин-код карты" alone -> keep; "пин-код карты + номер карты" -> mask both).
    """

    when_type: PIIType
    requires_nearby_type: PIIType
    window_chars: int = 300


DEFAULT_RELATIONSHIP_RULES: tuple[RelationshipRule, ...] = (
    RelationshipRule(PIIType.PIN, PIIType.CARD_NUMBER, window_chars=300),
)


def is_nearby(
    rule: RelationshipRule,
    text: str,
    candidate_start: int,
    candidate_end: int,
    other_start: int,
    other_end: int,
) -> bool:
    """Character-distance AND same-paragraph.

    A pure character radius is unsafe once documents concatenate many independent
    fragments (spec's long.jsonl stress case): a PIN belonging to one fragment can
    end up within a couple hundred characters of an unrelated CARD_NUMBER from the
    NEXT fragment purely because of how densely they're packed, wrongly forcing
    MASK on a PIN whose own fragment explicitly has no card number at all. Fragments
    are blank-line-separated, so a "\\n\\n" in the gap means they're unrelated.
    """
    gap_left = min(candidate_end, other_end)
    gap_right = max(candidate_start, other_start)
    if gap_left < gap_right and "\n\n" in text[gap_left:gap_right]:
        return False
    gap = max(candidate_start - other_end, other_start - candidate_end, 0)
    return gap <= rule.window_chars
