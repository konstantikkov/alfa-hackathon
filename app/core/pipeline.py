from __future__ import annotations

from app.config.consumers import ConsumerConfig
from app.context.overlap import resolve_overlaps
from app.core.enums import MaskingMode
from app.core.interfaces import PrivacyDecider
from app.core.models import Candidate, PrivacyDecision, TransformResult
from app.detectors.registry import DetectorRegistry
from app.policies.engine import PolicyEngine
from app.replacement.strategies import get_strategy


class PiiPipeline:
    """FAST PATH FIRST: detectors -> overlap resolution -> contextual decision ->
    policy -> replacement. No transformer/LLM call lives on this path (spec section 3).
    """

    def __init__(
        self,
        detectors: DetectorRegistry,
        decider: PrivacyDecider,
        policy_engine: PolicyEngine,
    ) -> None:
        self._detectors = detectors
        self._decider = decider
        self._policy_engine = policy_engine

    def analyze(self, text: str, consumer: ConsumerConfig) -> tuple[list[Candidate], list[PrivacyDecision]]:
        """Detection + contextual decision + policy, without replacement -- the part
        `/inspect` needs to show its work (candidates found, and why each was
        decided MASK/KEEP), split out from `process()` so the UI and the real
        pipeline can never drift apart.
        """
        candidates = self._detectors.detect_all(text)
        candidates = resolve_overlaps(candidates)

        decisions = self._decider.decide_batch(text, candidates)
        decisions = self._policy_engine.evaluate(text, decisions, candidates, consumer)
        return candidates, decisions

    def process(
        self,
        text: str,
        mode: MaskingMode,
        consumer: ConsumerConfig,
        session_seed: str,
    ) -> TransformResult:
        _candidates, decisions = self.analyze(text, consumer)
        strategy = get_strategy(mode)
        return strategy.transform(text, decisions, session_seed)
