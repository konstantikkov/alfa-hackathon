"""Structured Russian name mentions: role assignment, alias typing, and
shape-preserving rendering.

A mention is decomposed into ordered components (full words / initials) with
byte offsets inside the mention surface. Rendering a synthetic replacement is a
right-to-left splice over those offsets, so the mention's exact punctuation,
spacing and word order survive: "И.И. Иванову" -> "А.М. Петрову", never a
re-serialized canonical form.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.replacement.morphology import inflect_word, normalize_to_nominative

ROLE_SURNAME = "surname"
ROLE_FIRST = "first"
ROLE_PATRONYMIC = "patronymic"

_OFFSET_START = "offset_start"

# Grammar-role labels produced by classify_name_word -> our canonical roles.
_GRAMMEME_TO_ROLE = {"Surn": ROLE_SURNAME, "Name": ROLE_FIRST, "Patr": ROLE_PATRONYMIC}

ALIAS_FULL = "FULL"
ALIAS_NAME_SURNAME = "NAME_SURNAME"
ALIAS_NAME_PATRONYMIC = "NAME_PATRONYMIC"
ALIAS_SURNAME_INITIALS = "SURNAME_INITIALS"


@dataclass
class NameComponent:
    kind: str  # "word" | "initial"
    surface: str
    offset_start: int  # offsets inside the mention value
    offset_end: int
    role: str | None = None
    normalized: str | None = None  # nominative lowercase, words only
    letter: str | None = None  # initials only


def assign_roles(labels: list[str | None], word_count: int) -> list[str | None]:
    """Map per-word grammeme labels (Surn/Name/Patr/None) to canonical roles,
    resolving duplicates positionally by the two common Russian orders
    (Surname First Patronymic / First Patronymic Surname)."""
    roles: list[str | None] = [_GRAMMEME_TO_ROLE.get(label) if label else None for label in labels]
    taken = [r for r in roles if r]
    if len(set(taken)) == len(taken) and all(roles):
        return roles

    patr_idx = next((i for i, r in enumerate(roles) if r == ROLE_PATRONYMIC), None)
    if word_count == 3:
        if patr_idx == 1:
            return [ROLE_FIRST, ROLE_PATRONYMIC, ROLE_SURNAME]
        return [ROLE_SURNAME, ROLE_FIRST, ROLE_PATRONYMIC]
    if word_count == 2:
        if patr_idx is not None:
            other = 1 - patr_idx
            fixed: list[str | None] = [None, None]
            fixed[patr_idx] = ROLE_PATRONYMIC
            fixed[other] = ROLE_FIRST
            return fixed
        # "Name Surname" order is the default; a confident Surn label wins.
        if roles[0] == ROLE_SURNAME and roles[1] != ROLE_SURNAME:
            return [ROLE_SURNAME, ROLE_FIRST]
        return [ROLE_FIRST, ROLE_SURNAME]
    return roles


def alias_type_for(components: list[NameComponent]) -> str:
    kinds = [c.kind for c in components]
    if "initial" in kinds:
        return ALIAS_SURNAME_INITIALS
    roles = {c.role for c in components if c.kind == "word"}
    if len([c for c in components if c.kind == "word"]) >= 3:
        return ALIAS_FULL
    if ROLE_PATRONYMIC in roles:
        return ALIAS_NAME_PATRONYMIC
    return ALIAS_NAME_SURNAME


def components_to_metadata(components: list[NameComponent]) -> list[dict]:
    return [
        {
            "kind": c.kind,
            "surface": c.surface,
            _OFFSET_START: c.offset_start,
            "offset_end": c.offset_end,
            "role": c.role,
            "normalized": c.normalized,
            "letter": c.letter,
        }
        for c in components
    ]


def render_like_mention(
    mention_value: str,
    components: list[dict],
    synthetic_identity: dict,
    case: str,
    gender: str,
) -> str:
    """Substitute each component of the mention with the corresponding part of the
    synthetic identity, preserving the mention's exact layout. Raises KeyError /
    ValueError when a component has no resolvable role or synthetic part -- the
    caller treats that as low morphology confidence and falls back to TOKEN."""
    result = mention_value
    for comp in sorted(components, key=lambda c: c[_OFFSET_START], reverse=True):
        role = comp.get("role")
        if role is None:
            raise ValueError("unresolved name component role")
        # Role labels ARE the identity keys (surname / first / patronymic).
        part = synthetic_identity.get(role)
        if not part:
            raise ValueError(f"synthetic identity missing part {role}")
        replacement = part[0] + "." if comp["kind"] == "initial" else inflect_word(part, case, gender)
        result = result[: comp[_OFFSET_START]] + replacement + result[comp["offset_end"] :]
    return result


def normalized_word(word: str, gender: str | None) -> str:
    return normalize_to_nominative(word, gender).lower()
