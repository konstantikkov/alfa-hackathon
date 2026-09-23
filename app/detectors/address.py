from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_CITY = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?(?:\s[А-ЯЁ][а-яё]+)?"
_STREET = r"[А-ЯЁ][а-яёА-ЯЁ\-]+(?:\s[а-яёА-ЯЁ\-]+)?"

_FULL_WITH_INDEX_RE = re.compile(rf"\d{{6}},\s*г\.\s*{_CITY},\s*ул\.\s*{_STREET},\s*д\.\s*\d+,\s*кв\.\s*\d+")
_FULL_WORDS_RE = re.compile(rf"г\.\s*{_CITY},\s*ул\.\s*{_STREET},\s*дом\s*\d+,\s*квартира\s*\d+")
_COMPACT_RE = re.compile(rf"(?<![,\wА-Яа-я]){_CITY},\s*{_STREET},\s*\d+-\d+(?!\d)")


class AddressDetector(CandidateDetector):
    """Covers the structured Russian postal-address shapes this service targets:
    "<index>, г. <city>, ул. <street>, д. <n>, кв. <n>", the word-form variant, and
    a compact "<city>, <street>, <n>-<n>" form. Real-world address text is far more
    varied; this is a deliberate v1 scope (spec priorities: correctness on a defined
    surface first, breadth later).
    """

    name = "address"

    def detect(self, text: str) -> list[Candidate]:
        occupied: list[tuple[int, int]] = []
        candidates: list[Candidate] = []

        for pattern, confidence in (
            (_FULL_WITH_INDEX_RE, 0.95),
            (_FULL_WORDS_RE, 0.93),
            (_COMPACT_RE, 0.7),
        ):
            for m in pattern.finditer(text):
                if any(m.start() < e and s < m.end() for s, e in occupied):
                    continue
                candidates.append(
                    Candidate(
                        PIIType.ADDRESS,
                        m.group(),
                        m.start(),
                        m.end(),
                        self.name,
                        confidence,
                    )
                )
                occupied.append((m.start(), m.end()))

        return candidates
