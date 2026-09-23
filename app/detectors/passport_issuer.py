from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_RE = re.compile(
    r"(?:ГУ МВД России|УМВД России|Отделом? МВД России)\s+по\s+г\.\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
)


class PassportIssuerDetector(CandidateDetector):
    name = "passport_issuer"

    def detect(self, text: str) -> list[Candidate]:
        return [
            Candidate(PIIType.PASSPORT_ISSUER, m.group(), m.start(), m.end(), self.name, 0.9)
            for m in _RE.finditer(text)
        ]
