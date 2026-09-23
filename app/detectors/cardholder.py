from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_CARDHOLDER_RE = re.compile(r"(?i:держател[а-яё]*)[:\s]+([А-ЯЁ]{2,}(?:\s+[А-ЯЁ]{2,}){1,2})")


class CardholderDetector(CandidateDetector):
    name = "cardholder"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _CARDHOLDER_RE.finditer(text):
            value = m.group(1)
            if not value.isupper():
                continue
            candidates.append(Candidate(PIIType.CARDHOLDER, value, m.start(1), m.end(1), self.name, 0.9))
        return candidates
