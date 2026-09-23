from app.core.enums import Decision, MaskingMode, PIIType
from app.core.models import Candidate, PrivacyDecision
from app.demasking.demasker import ModeDemasker
from app.replacement.engine import build_transform_result

# Module-level constants for repeated literals (S1192).
_IVANOVU_IVANU_IVANOVICHU = "Иванову Ивану Ивановичу"
_IVANOV_IVAN_IVANOVICH = "Иванов Иван Иванович"

demasker = ModeDemasker()


def _name_candidate(value, start, end, gram_case, nominal_form):
    return Candidate(
        type=PIIType.FULL_NAME,
        value=value,
        start=start,
        end=end,
        detector="test",
        metadata={
            "gram_case": gram_case,
            "gender": "masc",
            "nominal_form": nominal_form,
        },
    )


def test_token_round_trip():
    text = "Клиент Иванов Иван Иванович обратился в банк."
    start = text.index("Иванов")
    end = start + len(_IVANOV_IVAN_IVANOVICH)
    candidate = _name_candidate(_IVANOV_IVAN_IVANOVICH, start, end, "nomn", _IVANOV_IVAN_IVANOVICH)
    decisions = [PrivacyDecision(candidate, Decision.MASK, 0.9)]

    masked = build_transform_result(text, decisions, MaskingMode.TOKEN, session_seed="rt-1")
    restored = demasker.demask(masked.text, masked.mappings, MaskingMode.TOKEN)

    assert restored == text


def test_synthetic_round_trip_when_llm_rewords_around_the_name():
    text = "Банк направил письмо Иванову Ивану Ивановичу."
    start = text.index("Иванову")
    end = start + len(_IVANOVU_IVANU_IVANOVICHU)
    candidate = _name_candidate(_IVANOVU_IVANU_IVANOVICHU, start, end, "datv", _IVANOV_IVAN_IVANOVICH)
    decisions = [PrivacyDecision(candidate, Decision.MASK, 0.9)]

    masked = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, session_seed="rt-2")
    synthetic_surname = masked.mappings[0].synthetic_nominal.split()[0]
    assert synthetic_surname in masked.text

    # Simulate an LLM response that mentions the synthetic person in a *different*
    # case than the one we originally emitted -- demasking must still find it.
    llm_response = f"Предложение можно отправить {masked.mappings[0].synthetic}."
    # masked.mappings[0].synthetic is already the nominative synthetic identity form;
    # build a dative-form LLM response instead to prove case-independent matching.
    from app.replacement.morphology import inflect_full_name

    identity = masked.mappings[0].metadata["synthetic_identity"]
    dative_synthetic = inflect_full_name(
        identity["surname"],
        identity["first"],
        identity["patronymic"],
        "datv",
        identity["gender"],
    )
    llm_response = f"Предложение можно отправить {dative_synthetic}."

    restored = demasker.demask(llm_response, masked.mappings, MaskingMode.SYNTHETIC)

    assert _IVANOVU_IVANU_IVANOVICHU in restored
    assert synthetic_surname not in restored


def test_generic_synthetic_demask_does_not_corrupt_substring_collisions():
    # Regression: a synthetic CVV like "030" can appear, by coincidence, as a
    # substring inside an unrelated (already-restored) card number. A naive
    # `str.replace` would corrupt the card digits; word-boundary-safe replace must not.
    from app.core.models import EntityMapping

    card_mapping = EntityMapping(
        entity_id="CARD_1",
        type=PIIType.CARD_NUMBER,
        original="0000000000030000",
        token="<CARD_1>",
        synthetic="9999999999999999",
        partial="",
        original_nominal="0000000000030000",
        synthetic_nominal="9999999999999999",
    )
    cvv_mapping = EntityMapping(
        entity_id="CVV_1",
        type=PIIType.CVV,
        original="094",
        token="<CVV_1>",
        synthetic="030",
        partial="",
        original_nominal="094",
        synthetic_nominal="030",
    )
    # "030" (the CVV's synthetic value) is, by coincidence, a substring of the
    # card's ORIGINAL number at positions 9-11 -- this is what triggers the bug
    # once the card mapping is processed first and restores its digits into the text.
    assert "030" in card_mapping.original

    masked_text = "Карта 9999999999999999, CVV 030."
    restored = demasker.demask(masked_text, [card_mapping, cvv_mapping], MaskingMode.SYNTHETIC)

    assert restored == "Карта 0000000000030000, CVV 094."


def test_unknown_token_is_left_untouched():
    mappings = []
    text = "Ответ содержит неизвестный токен <PERSON_1>."

    restored = demasker.demask(text, mappings, MaskingMode.TOKEN)

    assert restored == text
