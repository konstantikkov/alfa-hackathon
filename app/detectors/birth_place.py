from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_RE = re.compile(
    r"(?i:место\s+рождения)[^:;.]*[:\-—]\s*"
    r"((?:г\.\s*|город\s+)?[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?(?:,\s*Росси[ия][^,.;]*)?)"
)


class BirthPlaceDetector(CandidateDetector):
    name = "birth_place"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _RE.finditer(text):
            candidates.append(
                Candidate(
                    PIIType.BIRTH_PLACE,
                    m.group(1),
                    m.start(1),
                    m.end(1),
                    self.name,
                    0.85,
                )
            )
        return candidates
