from __future__ import annotations

import re

from app.config.consumers import ConsumerRegistry
from app.context.engine import ContextualPrivacyEngine
from app.core.enums import MaskingMode, PIIType
from app.core.pipeline import PiiPipeline
from app.demasking.demasker import ModeDemasker
from app.detectors.builtin import register_builtin_detectors
from app.detectors.fio import FullNameDetector
from app.detectors.registry import DetectorRegistry
from app.policies.engine import PolicyEngine

_SURNAME = "surname"
_GENDER = "gender"

# Module-level constants for repeated literals (S1192).
_SMIT_DZHON = "Смит Джон"
_SYNTHETIC_IDENTITY = "synthetic_identity"


def _pipeline():
    detectors = DetectorRegistry()
    register_builtin_detectors(detectors)
    return PiiPipeline(detectors, ContextualPrivacyEngine(), PolicyEngine())


def _consumer():
    return ConsumerRegistry.load("app/config/consumers.yaml").resolve(None)


def _mask(text: str, mode=MaskingMode.SYNTHETIC, seed="seed-1"):
    return _pipeline().process(text=text, mode=mode, consumer=_consumer(), session_seed=seed)


# --- detector: initials forms -------------------------------------------------


def test_detects_initials_then_surname():
    found = FullNameDetector().detect("Документы И. И. Иванова готовы к выдаче.")
    values = [c.value for c in found]
    assert "И. И. Иванова" in values


def test_detects_surname_then_initials():
    found = FullNameDetector().detect("Заявление подписал Иванов И. И. вчера вечером.")
    assert any(c.value.startswith("Иванов И. И") for c in found)


def test_initials_before_geography_not_detected():
    # "В. Новгород" -- Новгород is not a surname; must not be a candidate.
    found = FullNameDetector().detect("Поезд прибывает в В. Новгород по расписанию.")
    assert all("Новгород" not in c.value for c in found)


# --- resolution: merging ------------------------------------------------------


def test_full_and_inflected_and_initials_merge_into_one_entity():
    text = (
        "Клиент Иванов Иван Иванович оформил кредит. "
        "Позже Ивану Иванову позвонил менеджер. "
        "Документы И. И. Иванова готовы."
    )
    result = _mask(text)
    person_ids = {m.entity_id for m in result.mappings if m.type == PIIType.FULL_NAME}
    assert person_ids == {"PERSON_1"}
    # every mention got the same synthetic identity, in its own shape
    identity = result.mappings[0].metadata[_SYNTHETIC_IDENTITY]
    assert identity[_SURNAME] in result.text
    # As a standalone word: the synthetic patronymic may legitimately be
    # "Иванович", which contains the original surname as a substring.
    assert not re.search(r"\bИванов\b", result.text)


def test_surname_only_match_never_merges():
    # Two different Ivanovs: first name conflicts -> two entities.
    text = "Клиент Иванов Иван Петрович оформил вклад. Клиент Иванов Олег Сергеевич оформил кредит."
    result = _mask(text)
    person_ids = {m.entity_id for m in result.mappings if m.type == PIIType.FULL_NAME}
    assert len(person_ids) == 2


def test_ambiguous_initials_do_not_merge():
    # "И. Иванов" is compatible with both Иван and Игорь Ивановы -> new entity.
    text = (
        "Клиент Иванов Иван Петрович и клиент Иванов Игорь Сергеевич оформили кредиты. "
        "Подпись И. Иванова стоит на договоре."
    )
    result = _mask(text)
    person_ids = {m.entity_id for m in result.mappings if m.type == PIIType.FULL_NAME}
    assert len(person_ids) == 3


def test_gender_conflict_blocks_merge():
    text = "Клиентка Иванова Мария Петровна оформила вклад. Клиент Иванов Иван Петрович оформил кредит."
    result = _mask(text)
    person_ids = {m.entity_id for m in result.mappings if m.type == PIIType.FULL_NAME}
    assert len(person_ids) == 2


# --- shape preservation -------------------------------------------------------


def test_mention_shape_preserved_in_masked_text():
    text = "Клиент Иванов Иван Иванович оформил кредит. Документы И.И. Иванова готовы."
    result = _mask(text)
    identity = next(m.metadata[_SYNTHETIC_IDENTITY] for m in result.mappings if m.type == PIIType.FULL_NAME)
    f_init, p_init = identity["first"][0], identity["patronymic"][0]
    # compact "И.И." stays compact, with the synthetic initials
    assert f"{f_init}.{p_init}." in result.text


def test_round_trip_with_aliases_and_cases():
    text = (
        "Клиент Иванов Иван Иванович оформил кредит. "
        "Позже Ивану Иванову позвонил менеджер. "
        "Документы И. И. Иванова готовы."
    )
    result = _mask(text)
    demasked = ModeDemasker().demask(result.text, result.mappings, MaskingMode.SYNTHETIC)
    assert demasked == text


def test_llm_reworded_alias_still_demasks():
    text = "Клиент Иванов Иван Иванович оформил кредит."
    result = _mask(text)
    identity = result.mappings[0].metadata[_SYNTHETIC_IDENTITY]
    # LLM answers with a shape never present in the masked text: Name + Surname, dative
    from app.replacement.morphology import inflect_word

    llm_alias = (
        f"{inflect_word(identity['first'], 'datv', identity[_GENDER])} "
        f"{inflect_word(identity[_SURNAME], 'datv', identity[_GENDER])}"
    )
    llm_text = f"Мы направили уведомление {llm_alias} по указанному адресу."
    demasked = ModeDemasker().demask(llm_text, result.mappings, MaskingMode.SYNTHETIC)
    assert "Ивану Иванову" in demasked
    assert identity[_SURNAME] not in demasked
    # mention shape preserved: no patronymic added by demasking
    assert "Иванович" not in demasked


def test_morphology_failure_falls_back_to_token():
    from app.core.enums import Decision
    from app.core.models import Candidate, PrivacyDecision
    from app.replacement.engine import build_transform_result

    # name_parts with an unresolvable role -> render_like_mention raises -> token
    candidate = Candidate(
        type=PIIType.FULL_NAME,
        value=_SMIT_DZHON,
        start=0,
        end=9,
        detector="fio",
        metadata={
            "gram_case": "nomn",
            _GENDER: "masc",
            "nominal_form": _SMIT_DZHON,
            "identity_key": "person:смит джон",
            "name_parts": [
                {
                    "kind": "word",
                    "surface": "Смит",
                    "offset_start": 0,
                    "offset_end": 4,
                    "role": None,
                    "normalized": "смит",
                    "letter": None,
                },
                {
                    "kind": "word",
                    "surface": "Джон",
                    "offset_start": 5,
                    "offset_end": 9,
                    "role": None,
                    "normalized": "джон",
                    "letter": None,
                },
            ],
            "alias_type": "NAME_SURNAME",
        },
    )
    decision = PrivacyDecision(candidate, Decision.MASK, 0.9)
    result = build_transform_result("Смит Джон купил полис.", [decision], MaskingMode.SYNTHETIC, "seed")
    mapping = result.mappings[0]
    assert mapping.metadata.get("synthetic_fallback") == "token"
    assert mapping.token in result.text
    demasked = ModeDemasker().demask(result.text, result.mappings, MaskingMode.SYNTHETIC)
    assert _SMIT_DZHON in demasked
