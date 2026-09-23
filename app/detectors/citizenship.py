from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_RE = re.compile(r"(?i:гражданств[оа])\s*[:\-—]?\s*([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)")


class CitizenshipDetector(CandidateDetector):
    name = "citizenship"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _RE.finditer(text):
            candidates.append(
                Candidate(
                    PIIType.CITIZENSHIP,
                    m.group(1),
                    m.start(1),
                    m.end(1),
                    self.name,
                    0.85,
                )
            )
        return candidates
