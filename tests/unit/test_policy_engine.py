from app.config.consumers import ConsumerConfig
from app.core.enums import Decision, PIIType
from app.core.models import Candidate, PrivacyDecision
from app.policies.engine import PolicyEngine

# All-zero dummy fixtures; positions matter here, values do not.
_DUMMY_CARD_SPACED = "0000 0000 0000 0000"
_DUMMY_PIN = "0000"

engine = PolicyEngine()
default_consumer = ConsumerConfig(consumer_id="default")


def _mask_decision(pii_type, value, start, end):
    candidate = Candidate(type=pii_type, value=value, start=start, end=end, detector="test")
    return PrivacyDecision(candidate=candidate, decision=Decision.MASK, confidence=0.9)


def test_pin_without_card_is_downgraded_to_keep():
    text = " " * 44
    pin = _mask_decision(PIIType.PIN, _DUMMY_PIN, 40, 44)

    result = engine.evaluate(text, [pin], [pin.candidate], default_consumer)

    assert result[0].decision == Decision.KEEP
    assert any("relationship_rule_unmet" in r for r in result[0].reasons)


def test_pin_with_nearby_card_stays_masked():
    text = _DUMMY_CARD_SPACED + " " * 11 + _DUMMY_PIN
    card = _mask_decision(PIIType.CARD_NUMBER, _DUMMY_CARD_SPACED, 0, 19)
    pin = _mask_decision(PIIType.PIN, _DUMMY_PIN, 30, 34)
    candidates = [card.candidate, pin.candidate]

    result = engine.evaluate(text, [card, pin], candidates, default_consumer)

    decisions_by_type = {d.candidate.type: d.decision for d in result}
    assert decisions_by_type[PIIType.CARD_NUMBER] == Decision.MASK
    assert decisions_by_type[PIIType.PIN] == Decision.MASK


def test_pin_and_card_in_different_paragraphs_are_not_linked():
    # Regression: long documents concatenate independent fragments separated by a
    # blank line. A PIN from one fragment landing within `window_chars` of an
    # unrelated CARD_NUMBER from the NEXT fragment must not force MASK -- confirmed
    # against the actual long.jsonl stress dataset, where "ПИН-код пользователя: X.
    # Номер карты в сообщении отсутствует." (no card in this message) sat ~60 chars
    # before an unrelated card number belonging to the following fragment.
    gap = "Номер карты в сообщении отсутствует.\n\nСледующий фрагмент: "
    pin_text = f"ПИН-код пользователя: {_DUMMY_PIN}. "
    card_text = _DUMMY_CARD_SPACED
    text = pin_text + gap + card_text
    pin_start = pin_text.index(_DUMMY_PIN)
    pin = _mask_decision(PIIType.PIN, _DUMMY_PIN, pin_start, pin_start + 4)
    card_start = len(pin_text) + len(gap)
    card = _mask_decision(PIIType.CARD_NUMBER, card_text, card_start, card_start + len(card_text))

    result = engine.evaluate(text, [pin, card], [pin.candidate, card.candidate], default_consumer)

    decisions_by_type = {d.candidate.type: d.decision for d in result}
    assert decisions_by_type[PIIType.PIN] == Decision.KEEP
    assert decisions_by_type[PIIType.CARD_NUMBER] == Decision.MASK


def test_consumer_policy_excludes_type():
    consumer = ConsumerConfig(consumer_id="analytics", mask_types=frozenset({PIIType.EMAIL}))
    phone = _mask_decision(PIIType.PHONE, "+79000000000", 0, 12)

    result = engine.evaluate(" " * 12, [phone], [phone.candidate], consumer)

    assert result[0].decision == Decision.KEEP
    assert "consumer_policy_excludes_type" in result[0].reasons
