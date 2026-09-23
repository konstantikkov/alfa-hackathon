from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import Decision, MaskingMode, PIIType


@dataclass(frozen=True)
class Candidate:
    """A raw span found by a detector, before any privacy decision is made."""

    type: PIIType
    value: str
    start: int
    end: int
    detector: str
    detector_confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class PrivacyDecision:
    """Whether a specific candidate occurrence is personal data in context."""

    candidate: Candidate
    decision: Decision
    confidence: float
    reasons: tuple[str, ...] = ()

    @property
    def is_personal(self) -> bool:
        return self.decision == Decision.MASK


@dataclass
class EntityMapping:
    """Everything needed to demask one logical entity later."""

    entity_id: str
    type: PIIType
    original: str
    token: str
    synthetic: str
    partial: str
    original_nominal: str | None = None
    synthetic_nominal: str | None = None
    grammatical_case: str | None = None
    gender: str | None = None
    aliases: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectedEntity:
    """A decided-and-assigned-identity entity occurrence, ready for replacement."""

    entity_id: str
    decision: PrivacyDecision
    start: int
    end: int


@dataclass
class Occurrence:
    """One masked span with its coordinates in BOTH the original and masked text.

    Lets the service store fingerprints + occurrences instead of the full (up to
    100k-token) payloads: an exact retry re-splices replacements into the original
    payload the client just sent; an exact demask re-splices originals into the
    masked payload -- both O(len(text)) with no full-document persistence.
    """

    entity_id: str
    original_start: int
    original_end: int
    masked_start: int
    masked_end: int
    original: str
    masked: str


@dataclass
class TransformResult:
    text: str
    mode: MaskingMode
    mappings: list[EntityMapping]
    detected_entities: list[DetectedEntity]
    kept_entities: list[DetectedEntity]
    occurrences: list[Occurrence] = field(default_factory=list)
