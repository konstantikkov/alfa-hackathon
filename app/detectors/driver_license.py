from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.keyword_gate import has_any_keyword, window

_BARE_4_6_RE = re.compile(r"(?<!\d)(\d{4})[ ](\d{6})(?!\d)")
_BARE_2_2_6_RE = re.compile(r"(?<!\d)(\d{2})[ ](\d{2})[ ](\d{6})(?!\d)")
_BARE_10_RE = re.compile(r"(?<!\d)\d{10}(?!\d)")

_DL_KEYWORDS = ("водительск", "удостоверени", "в/у")


class DriverLicenseDetector(CandidateDetector):
    name = "driver_license"

    def detect(self, text: str) -> list[Candidate]:
        occupied: list[tuple[int, int]] = []
        candidates: list[Candidate] = []

        for pattern, confidence in (
            (_BARE_2_2_6_RE, 0.8),
            (_BARE_4_6_RE, 0.78),
            (_BARE_10_RE, 0.72),
        ):
            for m in pattern.finditer(text):
                if any(m.start() < e and s < m.end() for s, e in occupied):
                    continue
                if not has_any_keyword(window(text, m.start(), m.end()).lower(), _DL_KEYWORDS):
                    continue
                candidates.append(
                    Candidate(
                        PIIType.DRIVER_LICENSE,
                        m.group(),
                        m.start(),
                        m.end(),
                        self.name,
                        confidence,
                    )
                )
                occupied.append((m.start(), m.end()))

        return candidates
