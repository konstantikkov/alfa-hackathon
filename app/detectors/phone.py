from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

_RU_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
_INTL_PHONE_RE = re.compile(r"(?<!\d)\+\d{1,3}(?:[\s\-]?\d{2,4}){2,5}(?!\d)")


def normalize_ru_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return "+" + digits if digits else raw


class PhoneDetector(CandidateDetector):
    name = "phone"

    def detect(self, text: str) -> list[Candidate]:
        candidates: list[Candidate] = []
        occupied: list[tuple[int, int]] = []

        for m in _RU_PHONE_RE.finditer(text):
            candidates.append(
                Candidate(
                    PIIType.PHONE,
                    m.group(),
                    m.start(),
                    m.end(),
                    self.name,
                    0.95,
                    metadata={"normalized": normalize_ru_phone(m.group())},
                )
            )
            occupied.append((m.start(), m.end()))

        for m in _INTL_PHONE_RE.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in occupied):
                continue
            digit_count = sum(ch.isdigit() for ch in m.group())
            if not (8 <= digit_count <= 15):
                continue
            candidates.append(Candidate(PIIType.PHONE, m.group(), m.start(), m.end(), self.name, 0.75))

        return candidates
