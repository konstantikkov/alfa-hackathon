from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.checksums import luhn_valid

_CARD_RE = re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)")


class CardDetector(CandidateDetector):
    name = "card"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _CARD_RE.finditer(text):
            digits = re.sub(r"[ \-]", "", m.group())
            if 13 <= len(digits) <= 19 and luhn_valid(digits):
                candidates.append(
                    Candidate(
                        PIIType.CARD_NUMBER,
                        m.group(),
                        m.start(),
                        m.end(),
                        self.name,
                        0.95,
                        metadata={"normalized": digits},
                    )
                )
        return candidates
