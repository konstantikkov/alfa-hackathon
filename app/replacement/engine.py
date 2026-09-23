from __future__ import annotations

import hashlib

from app.core.enums import MaskingMode, PIIType
from app.core.models import (
    DetectedEntity,
    EntityMapping,
    Occurrence,
    PrivacyDecision,
    TransformResult,
)
from app.entity_resolution.naming import render_like_mention
from app.replacement.identity import EntityGroup, group_personal_decisions
from app.replacement.morphology import CASES, inflect_full_name, inflect_word
from app.replacement.partial import partial_mask
from app.replacement.synthetic_values import SyntheticGenerator
from app.replacement.tokens import token_for

# Module-level constants for repeated literals (S1192).
_FIRST = "first"
_GENDER = "gender"
_PATRONYMIC = "patronymic"
_SURNAME = "surname"
_SYNTH_FALLBACK = "synthetic_fallback"
_FALLBACK_TOKEN = "token"

_NAME_TYPES = (PIIType.FULL_NAME, PIIType.CARDHOLDER)


def _entity_seed(session_seed: str, identity_key: str) -> int:
    digest = hashlib.sha256(f"{session_seed}:{identity_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


_MAX_COLLISION_RETRIES = 20


def _alias_shape_collides(identity: dict | None, source_text: str) -> bool:
    """The demasker searches two-word alias shapes of a synthetic identity in
    every grammatical case; any such surface already present in the document
    (e.g. a kept public figure sharing the first name + patronymic) would be
    falsely rewritten at demask time, so it disqualifies the draw."""
    if not identity:
        return False
    gender = identity.get(_GENDER) or "masc"
    first = identity.get(_FIRST) or ""
    surname = identity.get(_SURNAME) or ""
    patronymic = identity.get(_PATRONYMIC) or ""
    pairs = [(a, b) for a, b in ((first, patronymic), (first, surname), (surname, first)) if a and b]
    return any(
        f"{inflect_word(a, case, gender)} {inflect_word(b, case, gender)}" in source_text
        for a, b in pairs
        for case in CASES
    )


def _build_mapping(group: EntityGroup, session_seed: str, used: set[str], source_text: str) -> EntityMapping:
    nominal = group.original_nominal
    first_case = group.decisions[0].candidate.metadata.get("gram_case") or "nomn"
    metadata: dict = {}

    # Low-cardinality pools (e.g. 6 countries) mean two DIFFERENT entities of the
    # same type in one document can otherwise get the SAME synthetic value by pure
    # chance. That's harmless for masking itself, but the general (LLM-reworded-text)
    # demasking path matches by value, not position -- an unavoidable ambiguity if
    # two originals map to one synthetic string. Retrying with a bumped seed until
    # the value is unique within this document/type removes the ambiguity for as
    # long as the pool has room; confirmed by a real collision on long.jsonl's
    # 100k-token stress case (two independent CITIZENSHIP entities both drawing
    # "Россия").
    synth_nominal = None
    identity = None
    for attempt in range(_MAX_COLLISION_RETRIES):
        seed = _entity_seed(session_seed, group.identity_key) + attempt
        gen = SyntheticGenerator(seed)
        if group.type in _NAME_TYPES:
            synth_nominal, identity = gen.full_name_in_case(nominal, group.gender, "nomn")
        else:
            synth_nominal = gen.generic(group.type, nominal)
        if (
            synth_nominal
            and synth_nominal not in used
            and synth_nominal not in source_text
            and not _alias_shape_collides(identity, source_text)
        ):
            break
    else:
        # Finite pools can be exhausted. Never accept an ambiguous or unchanged
        # surrogate simply because the retry budget ran out.
        synth_nominal = token_for(group.entity_id)
        identity = None
        metadata[_SYNTH_FALLBACK] = _FALLBACK_TOKEN
    used.add(synth_nominal)
    if group.type in _NAME_TYPES:
        metadata["synthetic_identity"] = identity
    if group.resolved is not None:
        metadata["original_identity"] = {
            _SURNAME: group.resolved.surfaces.get(_SURNAME),
            _FIRST: group.resolved.surfaces.get(_FIRST),
            _PATRONYMIC: group.resolved.surfaces.get(_PATRONYMIC),
            _GENDER: group.gender,
        }
        metadata["alias_types"] = sorted(group.resolved.alias_types)

    aliases = sorted({d.candidate.value for d in group.decisions})
    # TOKEN mode collapses every occurrence to the same `<ENTITY_N>` token, so there
    # is no per-occurrence case information left to restore from at demask time --
    # the best we can do is the exact surface form of the *first* mention (perfect
    # round-trip for the common single-mention case; repeated mentions in other
    # grammatical cases fall back to this form, a known TOKEN-mode limitation that
    # SYNTHETIC mode does not have).
    first_occurrence_surface = group.decisions[0].candidate.value

    return EntityMapping(
        entity_id=group.entity_id,
        type=group.type,
        original=first_occurrence_surface,
        token=token_for(group.entity_id),
        synthetic=synth_nominal,
        partial=partial_mask(group.type, nominal),
        original_nominal=nominal,
        synthetic_nominal=synth_nominal,
        grammatical_case=first_case,
        gender=group.gender,
        aliases=aliases,
        metadata=metadata,
    )


def _synthetic_replacement(decision: PrivacyDecision, mapping: EntityMapping) -> str:
    """Shape-preserving synthetic surface for one occurrence: "И.И. Иванову"
    becomes "А.М. Петрову", "Иван Иванов" becomes "Алексей Петров" -- same
    components, order, punctuation and grammatical case as the mention.

    Raises on unresolvable morphology; the caller downgrades that occurrence's
    entity to TOKEN (never emit a synthetic name we cannot reliably map back).
    """
    if mapping.metadata.get(_SYNTH_FALLBACK) == _FALLBACK_TOKEN:
        return mapping.token
    if mapping.type in _NAME_TYPES:
        identity = mapping.metadata.get("synthetic_identity")
        occurrence_case = decision.candidate.metadata.get("gram_case") or mapping.grammatical_case or "nomn"
        components = decision.candidate.metadata.get("name_parts")
        if identity and components:
            return render_like_mention(
                decision.candidate.value,
                components,
                identity,
                occurrence_case,
                identity.get(_GENDER) or "masc",
            )
        if identity:
            return inflect_full_name(
                identity[_SURNAME],
                identity[_FIRST],
                identity[_PATRONYMIC],
                occurrence_case,
                identity[_GENDER],
            )
    return mapping.synthetic


def _occurrence_replacement(mode: MaskingMode, decision: PrivacyDecision, mapping: EntityMapping) -> str:
    if mode == MaskingMode.PARTIAL:
        return partial_mask(decision.candidate.type, decision.candidate.value)
    if mode == MaskingMode.TOKEN:
        return mapping.token
    try:
        replacement = _synthetic_replacement(decision, mapping)
    except Exception:
        mapping.metadata[_SYNTH_FALLBACK] = _FALLBACK_TOKEN
        return mapping.token
    if replacement == decision.candidate.value:
        return mapping.token
    return replacement


def _splice_spans(text: str, spans: list[tuple[int, int, str, str]]) -> tuple[str, list[Occurrence]]:
    occurrences: list[Occurrence] = []
    pieces: list[str] = []
    cursor = 0
    offset = 0  # masked position - original position, accumulated left to right
    for start, end, replacement, entity_id in spans:
        pieces.append(text[cursor:start])
        pieces.append(replacement)
        occurrences.append(
            Occurrence(
                entity_id=entity_id,
                original_start=start,
                original_end=end,
                masked_start=start + offset,
                masked_end=start + offset + len(replacement),
                original=text[start:end],
                masked=replacement,
            )
        )
        offset += len(replacement) - (end - start)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), occurrences


def build_transform_result(
    text: str, decisions: list[PrivacyDecision], mode: MaskingMode, session_seed: str
) -> TransformResult:
    groups = group_personal_decisions(decisions)
    entity_by_key = {g.identity_key: g for g in groups}
    used: set[str] = set()
    mappings = {g.identity_key: _build_mapping(g, session_seed, used, text) for g in groups}

    decision_to_key: dict[int, str] = {}
    for group in groups:
        for decision in group.decisions:
            decision_to_key[id(decision)] = group.identity_key

    spans: list[tuple[int, int, str, str]] = []
    detected_entities: list[DetectedEntity] = []
    kept_entities: list[DetectedEntity] = []

    for decision in decisions:
        if not decision.is_personal:
            kept_entities.append(
                DetectedEntity("", decision, decision.candidate.start, decision.candidate.end)
            )
            continue

        key = decision_to_key[id(decision)]
        group = entity_by_key[key]
        mapping = mappings[key]
        detected_entities.append(
            DetectedEntity(
                group.entity_id,
                decision,
                decision.candidate.start,
                decision.candidate.end,
            )
        )

        spans.append(
            (
                decision.candidate.start,
                decision.candidate.end,
                _occurrence_replacement(mode, decision, mapping),
                group.entity_id,
            )
        )

    spans.sort(key=lambda s: s[0])
    result_text, occurrences = _splice_spans(text, spans)

    return TransformResult(
        text=result_text,
        mode=mode,
        mappings=list(mappings.values()),
        detected_entities=detected_entities,
        kept_entities=kept_entities,
        occurrences=occurrences,
    )
