from __future__ import annotations

import time
from abc import ABC, abstractmethod

from app.core.enums import MappingState, MaskingMode
from app.core.models import Candidate, EntityMapping, PrivacyDecision, TransformResult


class CandidateDetector(ABC):
    """One narrow-purpose detector: regex/dictionary/checksum based, no side effects."""

    name: str

    @abstractmethod
    def detect(self, text: str) -> list[Candidate]:
        raise NotImplementedError


class PrivacyDecider(ABC):
    """Decides MASK/KEEP/UNCERTAIN for a candidate given the full candidate set as context."""

    @abstractmethod
    def decide(self, text: str, candidate: Candidate, all_candidates: list[Candidate]) -> PrivacyDecision:
        raise NotImplementedError

    def decide_batch(
        self,
        text: str,
        candidates: list[Candidate],
        all_candidates: list[Candidate] | None = None,
    ) -> list[PrivacyDecision]:
        """Batch entry point; implementations with batch-cheap models override this."""
        context = all_candidates if all_candidates is not None else candidates
        return [self.decide(text, c, context) for c in candidates]


class MaskingStrategy(ABC):
    """Turns a text + resolved personal decisions into a transformed text + mappings."""

    mode: MaskingMode

    @abstractmethod
    def transform(self, text: str, decisions: list[PrivacyDecision], session_seed: str) -> TransformResult:
        raise NotImplementedError


class Demasker(ABC):
    @abstractmethod
    def demask(self, text: str, mappings: list[EntityMapping], mode: MaskingMode) -> str:
        raise NotImplementedError


class MappingStore(ABC):
    """Thin Redis (or in-memory) abstraction. No detection/policy logic lives here.

    Pipeline computation must be a pure function of (payload_id, text) so that two
    concurrent "first" requests for the same payload_id can each compute independently
    and still agree on content; `save_if_absent` then makes persistence itself atomic
    (Redis SET NX / an in-memory compare-and-set) so exactly one write wins the race
    without needing a separate lock+poll dance.
    """

    @abstractmethod
    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        """Atomically create the record iff it doesn't exist. Returns True iff created."""
        raise NotImplementedError

    @abstractmethod
    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, payload_id: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, payload_id: str) -> None:
        raise NotImplementedError

    def mark_demasked(self, payload_id: str, grace_seconds: int) -> None:
        """Move the mapping into DEMASKED_GRACE: keep it just long enough for the
        client to retry a demask whose response was lost, then let TTL purge it."""
        record = self.get(payload_id)
        if record is None:
            return
        record["state"] = MappingState.DEMASKED_GRACE.value
        record["demasked_at"] = time.time()
        self.update(payload_id, record, grace_seconds)

    def health(self) -> dict:
        return {"backend": type(self).__name__, "ok": True, "state": "closed"}
