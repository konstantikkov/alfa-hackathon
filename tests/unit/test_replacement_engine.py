from app.core.enums import Decision, MaskingMode, PIIType
from app.core.models import Candidate, PrivacyDecision
from app.replacement.engine import build_transform_result

# Module-level constants for repeated literals (S1192).
_BELARUS = "Беларусь"
_IVANOV = "Иванов"
_IVANOV_IVAN_IVANOVICH = "Иванов Иван Иванович"
_KAZAHSTAN = "Казахстан"
_PETROV_PETR_PETROVICH = "Петров Петр Петрович"


def _name_candidate(value, start, end, gram_case, gender="masc", nominal_form=None, identity_key=None):
    return Candidate(
        type=PIIType.FULL_NAME,
        value=value,
        start=start,
        end=end,
        detector="test",
        metadata={
            "gram_case": gram_case,
            "gender": gender,
            "nominal_form": nominal_form or value,
            "identity_key": identity_key or "person:иванов иван иванович",
        },
    )


def test_token_mode_assigns_stable_ids_in_appearance_order():
    text = "Клиент Иванов Иван Иванович и клиент Петров Петр Петрович."
    c1 = _name_candidate(
        _IVANOV_IVAN_IVANOVICH,
        text.index(_IVANOV),
        text.index("Иванович") + len("Иванович"),
        "nomn",
    )
    c2_start = text.index("Петров")
    c2 = Candidate(
        type=PIIType.FULL_NAME,
        value=_PETROV_PETR_PETROVICH,
        start=c2_start,
        end=c2_start + len(_PETROV_PETR_PETROVICH),
        detector="test",
        metadata={
            "gram_case": "nomn",
            "gender": "masc",
            "nominal_form": _PETROV_PETR_PETROVICH,
            "identity_key": "person:петров петр петрович",
        },
    )
    decisions = [
        PrivacyDecision(c1, Decision.MASK, 0.9),
        PrivacyDecision(c2, Decision.MASK, 0.9),
    ]

    result = build_transform_result(text, decisions, MaskingMode.TOKEN, session_seed="req-1")

    assert "<PERSON_1>" in result.text
    assert "<PERSON_2>" in result.text
    assert _IVANOV not in result.text


def test_repeated_person_gets_one_entity_id_and_consistent_synthetic_identity():
    text = "С Ивановым Иваном Ивановичем обсудили заявку. Позже письмо отправили Иванову Ивану Ивановичу."
    ins_start = text.index("Ивановым")
    ins_end = ins_start + len("Ивановым Иваном Ивановичем")
    c1 = _name_candidate(
        "Ивановым Иваном Ивановичем",
        ins_start,
        ins_end,
        "ablt",
        nominal_form=_IVANOV_IVAN_IVANOVICH,
    )
    dat_start = text.index("Иванову")
    dat_end = dat_start + len("Иванову Ивану Ивановичу")
    c2 = _name_candidate(
        "Иванову Ивану Ивановичу",
        dat_start,
        dat_end,
        "datv",
        nominal_form=_IVANOV_IVAN_IVANOVICH,
    )
    decisions = [
        PrivacyDecision(c1, Decision.MASK, 0.9),
        PrivacyDecision(c2, Decision.MASK, 0.9),
    ]

    result = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, session_seed="req-2")

    assert len(result.mappings) == 1
    mapping = result.mappings[0]
    assert mapping.original_nominal == _IVANOV_IVAN_IVANOVICH
    assert mapping.synthetic_nominal != mapping.original_nominal
    # Both occurrences must use the same synthetic surname (same person, same identity).
    synthetic_surname = mapping.synthetic_nominal.split()[0]
    assert result.text.count(synthetic_surname) == 2


def test_synthetic_full_name_is_never_identical_to_original():
    text = "Клиент Иванов Иван Иванович обратился в банк."
    start = text.index(_IVANOV)
    end = start + len(_IVANOV_IVAN_IVANOVICH)
    c1 = _name_candidate(_IVANOV_IVAN_IVANOVICH, start, end, "nomn")
    decisions = [PrivacyDecision(c1, Decision.MASK, 0.9)]

    result = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, session_seed="req-3")

    assert result.mappings[0].synthetic_nominal != _IVANOV_IVAN_IVANOVICH
    assert _IVANOV_IVAN_IVANOVICH not in result.text


def test_partial_masking_uses_initials_for_names():
    text = "Клиент Иванов Иван Иванович обратился в банк."
    start = text.index(_IVANOV)
    end = start + len(_IVANOV_IVAN_IVANOVICH)
    c1 = _name_candidate(_IVANOV_IVAN_IVANOVICH, start, end, "nomn")
    decisions = [PrivacyDecision(c1, Decision.MASK, 0.9)]

    result = build_transform_result(text, decisions, MaskingMode.PARTIAL, session_seed="req-4")

    assert "И. И. И." in result.text


def test_distinct_entities_of_same_type_get_distinct_synthetic_values():
    # Regression: two DIFFERENT citizenship entities in one document could get the
    # SAME synthetic value from the small country pool by pure chance, making the
    # general (value-based) demasking path unable to tell which restores to which.
    text = "Гражданство первого клиента: Беларусь. Гражданство второго клиента: Казахстан."
    v1_start = text.index(_BELARUS)
    v2_start = text.index(_KAZAHSTAN)
    c1 = Candidate(PIIType.CITIZENSHIP, _BELARUS, v1_start, v1_start + len(_BELARUS), "test")
    c2 = Candidate(PIIType.CITIZENSHIP, _KAZAHSTAN, v2_start, v2_start + len(_KAZAHSTAN), "test")
    decisions = [
        PrivacyDecision(c1, Decision.MASK, 0.9),
        PrivacyDecision(c2, Decision.MASK, 0.9),
    ]

    result = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, session_seed="collision-test")

    synthetic_values = [m.synthetic for m in result.mappings]
    assert len(synthetic_values) == len(set(synthetic_values)), synthetic_values


def test_keep_decisions_are_not_transformed():
    text = "Отделение банка по адресу Москва, Тверская, 10."
    value = "Москва, Тверская, 10"
    start = text.index(value)
    candidate = Candidate(PIIType.ADDRESS, value, start, start + len(value), "test")
    decisions = [PrivacyDecision(candidate, Decision.KEEP, 0.9)]

    result = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, session_seed="req-5")

    assert result.text == text
    assert result.mappings == []
    assert len(result.kept_entities) == 1
