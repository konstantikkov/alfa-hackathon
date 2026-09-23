from __future__ import annotations

import logging

from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

logger = logging.getLogger("app.detectors.registry")


class DetectorRegistry:
    """Holds independent detectors. One throwing detector never takes down the others."""

    def __init__(self) -> None:
        self._detectors: list[CandidateDetector] = []

    def register(self, detector: CandidateDetector) -> None:
        self._detectors.append(detector)

    def detect_all(self, text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        for detector in self._detectors:
            try:
                candidates.extend(detector.detect(text))
            except Exception:
                logger.exception("detector_failed", extra={"detector": detector.name})
        return candidates
