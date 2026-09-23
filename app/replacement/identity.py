from __future__ import annotations

from dataclasses import dataclass, field

from app.core.enums import PIIType
from app.core.models import PrivacyDecision
from app.entity_resolution.resolver import ResolvedPerson, resolve_person_entities
from app.replacement.tokens import TOKEN_PREFIX


@dataclass
class EntityGroup:
    entity_id: str
    identity_key: str
    decisions: list[PrivacyDecision] = field(default_factory=list)
    resolved: ResolvedPerson | None = None

    @property
    def type(self):
        return self.decisions[0].candidate.type

    @property
    def representative_value(self) -> str:
        return self.decisions[0].candidate.value

    @property
    def gender(self) -> str | None:
        if self.resolved is not None and self.resolved.gender:
            return self.resolved.gender
        for d in self.decisions:
            g = d.candidate.metadata.get("gender")
            if g:
                return g
        return None

    @property
    def original_nominal(self) -> str:
        if self.resolved is not None and self.resolved.nominal:
            return self.resolved.nominal
        for d in self.decisions:
            nominal = d.candidate.metadata.get("nominal_form")
            if nominal:
                return nominal
        return self.representative_value


def identity_key_for(decision: PrivacyDecision) -> str:
    explicit = decision.candidate.metadata.get("identity_key")
    if explicit:
        return explicit
    return f"{decision.candidate.type.value}:{decision.candidate.value.strip().lower()}"


def group_personal_decisions(decisions: list[PrivacyDecision]) -> list[EntityGroup]:
    """One entity_id per logical identity, in first-appearance order -- so
    `<PERSON_1>` always refers to whoever is named first in the text.

    FULL_NAME mentions go through conservative entity resolution (aliases like
    "Иван Иванов" / "Ивану Иванову" / "И. И. Иванову" collapse into one person
    when the evidence suffices); every other type groups by exact identity key.
    """
    personal = [d for d in decisions if d.is_personal]

    persons = resolve_person_entities(
        [
            d
            for d in personal
            if d.candidate.type == PIIType.FULL_NAME and d.candidate.metadata.get("name_parts")
        ]
    )
    resolved_by_decision: dict[int, ResolvedPerson] = {}
    for person in persons:
        for d in person.decisions:
            resolved_by_decision[id(d)] = person

    order: list[str] = []
    groups: dict[str, EntityGroup] = {}
    counters: dict[str, int] = {}

    for decision in sorted(personal, key=lambda d: d.candidate.start):
        person = resolved_by_decision.get(id(decision))
        key = person.identity_key if person is not None else identity_key_for(decision)
        if key not in groups:
            prefix = TOKEN_PREFIX.get(decision.candidate.type, decision.candidate.type.value)
            counters[prefix] = counters.get(prefix, 0) + 1
            entity_id = f"{prefix}_{counters[prefix]}"
            groups[key] = EntityGroup(entity_id=entity_id, identity_key=key, resolved=person)
            order.append(key)
        groups[key].decisions.append(decision)

    return [groups[k] for k in order]
