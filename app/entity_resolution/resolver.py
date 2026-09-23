"""Conservative in-document entity resolution for person mentions.

Links "Иван Иванов", "Ивану Иванову" and "И. И. Иванову" into one entity when
the evidence is sufficient; a bare surname is never enough, and an ambiguous
mention (compatible with two established entities) starts its own entity --
a false merge leaks one person's data under another person's identity, which is
strictly worse than issuing two entity_ids for the same human.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import PrivacyDecision
from app.entity_resolution.naming import (
    ROLE_FIRST,
    ROLE_PATRONYMIC,
    ROLE_SURNAME,
)

_NORMALIZED = "normalized"


@dataclass
class ResolvedPerson:
    ordinal: int
    decisions: list[PrivacyDecision] = field(default_factory=list)
    # canonical knowledge, accumulated across mentions: role -> normalized word
    parts: dict[str, str] = field(default_factory=dict)
    # role -> surface (nominative, capitalized) for rendering originals
    surfaces: dict[str, str] = field(default_factory=dict)
    gender: str | None = None
    alias_types: set[str] = field(default_factory=set)

    @property
    def identity_key(self) -> str:
        canonical = "|".join(self.parts.get(r, "") for r in (ROLE_SURNAME, ROLE_FIRST, ROLE_PATRONYMIC))
        return f"person:{canonical}#{self.ordinal}"

    @property
    def nominal(self) -> str:
        words = [self.surfaces[r] for r in (ROLE_SURNAME, ROLE_FIRST, ROLE_PATRONYMIC) if r in self.surfaces]
        return " ".join(words)


def _mention_words(components: list[dict]) -> list[dict]:
    return [c for c in components if c["kind"] == "word" and c.get(_NORMALIZED)]


def _mention_initials(components: list[dict]) -> list[dict]:
    return [c for c in components if c["kind"] == "initial"]


def _gender_conflict(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a != b


def _assign_word(
    entity: ResolvedPerson,
    comp: dict,
    idx: int,
    assignment: dict[int, str],
    matched_roles: set[str],
    new_info: dict[int, str],
) -> bool:
    """Place one full word; False means an unresolvable conflict."""
    role = comp.get("role")
    if role and role in matched_roles:
        return False  # two surnames / two first names in one mention
    if role and role in entity.parts:
        if entity.parts[role] != comp[_NORMALIZED]:
            return False  # explicit conflict: different surname/name/patronymic
        assignment[idx] = role
        matched_roles.add(role)
        return True
    if role:
        new_info[idx] = role
        return True
    # unlabeled word: match by value against any not-yet-used known part
    matched = next(
        (r for r, w in entity.parts.items() if w == comp[_NORMALIZED] and r not in matched_roles),
        None,
    )
    if matched is None:
        return False
    assignment[idx] = matched
    matched_roles.add(matched)
    return True


def _match_words(
    entity: ResolvedPerson, components: list[dict], words: list[dict]
) -> tuple[dict[int, str], set[str], dict[int, str]] | None:
    """Match full-word components against the entity's known parts.

    Returns (assignment, matched_roles, new_info) or None on conflict. A word
    the entity has not seen yet is acceptable only when its morphological role
    label is confident and the entity has no word in that role -- it becomes
    new knowledge on merge, not evidence for it.
    """
    assignment: dict[int, str] = {}
    matched_roles: set[str] = set()
    new_info: dict[int, str] = {}

    for comp in words:
        if not _assign_word(entity, comp, components.index(comp), assignment, matched_roles, new_info):
            return None
    return assignment, matched_roles, new_info


def _match_initials(
    entity: ResolvedPerson,
    components: list[dict],
    initials: list[dict],
    assignment: dict[int, str],
) -> dict[int, str] | None:
    """Surname + initials: every initial must match the first letter of a
    distinct KNOWN part, in canonical order (first -> first name, second ->
    patronymic). An unknown part makes the initial unverifiable -- and an
    initial consistent with anything is exactly why it is NOT evidence."""
    expected = (ROLE_FIRST, ROLE_PATRONYMIC)
    for i, comp in enumerate(initials):
        if i >= len(expected):
            return None
        role = expected[i]
        known = entity.parts.get(role)
        if known is None or known[0].upper() != comp["letter"].upper():
            return None
        assignment[components.index(comp)] = role
    return assignment


def _compatible(entity: ResolvedPerson, components: list[dict], gender: str | None) -> dict | None:
    """None if incompatible; else {component_index: role} assignment.

    Merge evidence must include a surname match PLUS first-name (or patronymic
    or verified-initials) evidence.
    """
    if _gender_conflict(entity.gender, gender):
        return None

    words = _mention_words(components)
    initials = _mention_initials(components)
    matched = _match_words(entity, components, words)
    if matched is None:
        return None
    assignment, matched_roles, new_info = matched

    if ROLE_SURNAME not in matched_roles:
        return None
    if initials:
        return _match_initials(entity, components, initials, assignment)
    if not words or not ({ROLE_FIRST, ROLE_PATRONYMIC} & matched_roles):
        return None
    assignment.update(new_info)
    return assignment


def _absorb(
    entity: ResolvedPerson,
    decision: PrivacyDecision,
    components: list[dict],
    gender: str | None,
    alias_type: str,
) -> None:
    entity.decisions.append(decision)
    entity.alias_types.add(alias_type)
    if entity.gender is None and gender:
        entity.gender = gender
    for comp in _mention_words(components):
        role = comp.get("role")
        if role and role not in entity.parts:
            entity.parts[role] = comp[_NORMALIZED]
            entity.surfaces[role] = comp[_NORMALIZED].capitalize()


def _annotate_roles(components: list[dict], assignment: dict[int, str]) -> None:
    for idx, role in assignment.items():
        components[idx]["role"] = role


def resolve_person_entities(decisions: list[PrivacyDecision]) -> list[ResolvedPerson]:
    """Group FULL_NAME decisions (sorted by position) into resolved persons."""
    entities: list[ResolvedPerson] = []

    for decision in sorted(decisions, key=lambda d: d.candidate.start):
        meta = decision.candidate.metadata
        components: list[dict] = meta.get("name_parts") or []
        gender = meta.get("gender")
        alias_type = meta.get("alias_type") or "FULL"

        matches: list[tuple[ResolvedPerson, dict]] = []
        for entity in entities:
            assignment = _compatible(entity, components, gender)
            if assignment is not None:
                matches.append((entity, assignment))

        if len(matches) == 1:
            entity, assignment = matches[0]
            _annotate_roles(components, assignment)
            _absorb(entity, decision, components, gender, alias_type)
            continue

        # Zero matches -> genuinely new person. Two+ matches -> ambiguous; a new
        # entity is the conservative resolution (never guess between people).
        entity = ResolvedPerson(ordinal=len(entities) + 1, gender=gender)
        _absorb(entity, decision, components, gender, alias_type)
        entities.append(entity)

    return entities
