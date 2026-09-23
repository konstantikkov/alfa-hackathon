from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.keyword_gate import has_any_keyword, window

_SERIES_WORD_RE = re.compile(r"серия\s*(\d{4})\s*(?:№|номер)?\s*(\d{6})", re.IGNORECASE)
_NUMBER_SIGN_RE = re.compile(r"(?<!\d)(\d{4})\s*№\s*(\d{6})(?!\d)")
_BARE_4_6_RE = re.compile(r"(?<!\d)(\d{4})[ ](\d{6})(?!\d)")
_BARE_2_2_6_RE = re.compile(r"(?<!\d)(\d{2})[ ](\d{2})[ ](\d{6})(?!\d)")

_PASSPORT_KEYWORDS = ("паспорт",)


class PassportDetector(CandidateDetector):
    name = "passport"

    def detect(self, text: str) -> list[Candidate]:
        occupied: list[tuple[int, int]] = []
        candidates: list[Candidate] = []

        for pattern, confidence, needs_keyword in (
            (_SERIES_WORD_RE, 0.97, False),
            (_NUMBER_SIGN_RE, 0.95, False),
            (_BARE_2_2_6_RE, 0.75, True),
            (_BARE_4_6_RE, 0.7, True),
        ):
            for m in pattern.finditer(text):
                if any(m.start() < e and s < m.end() for s, e in occupied):
                    continue
                if needs_keyword and not has_any_keyword(
                    window(text, m.start(), m.end()).lower(), _PASSPORT_KEYWORDS
                ):
                    continue
                candidates.append(
                    Candidate(
                        PIIType.PASSPORT,
                        m.group(),
                        m.start(),
                        m.end(),
                        self.name,
                        confidence,
                    )
                )
                occupied.append((m.start(), m.end()))

        return candidates
