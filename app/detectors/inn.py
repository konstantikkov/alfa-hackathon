from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.checksums import inn_valid

_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)")


class InnDetector(CandidateDetector):
    name = "inn"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _DIGIT_RUN_RE.finditer(text):
            digits = m.group()
            if inn_valid(digits):
                candidates.append(Candidate(PIIType.INN, digits, m.start(), m.end(), self.name, 0.9))
        return candidates
