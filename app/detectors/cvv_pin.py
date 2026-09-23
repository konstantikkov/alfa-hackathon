from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.keyword_gate import has_any_keyword, window

_CODE_RE = re.compile(r"(?<!\d)\d{3,4}(?!\d)")
_CVV_KEYWORDS = ("cvv", "cvc", "код безопасности")
_PIN_KEYWORDS = ("пин", "pin")


class CvvDetector(CandidateDetector):
    """Only a strong card-context keyword makes a bare 3/4-digit number a CVV
    candidate at all (spec section 7) -- format alone is far too ambiguous.
    """

    name = "cvv"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _CODE_RE.finditer(text):
            if len(m.group()) == 3 and has_any_keyword(
                window(text, m.start(), m.end(), radius=25).lower(), _CVV_KEYWORDS
            ):
                candidates.append(Candidate(PIIType.CVV, m.group(), m.start(), m.end(), self.name, 0.9))
        return candidates


class PinDetector(CandidateDetector):
    """A bare PIN is usually a confirmation/order code, not card data. The keyword
    gate here only decides whether to propose a candidate at all; whether it's
    actually personal (i.e. a card is mentioned nearby too) is a relationship rule
    in the policy engine, not this detector's job.
    """

    name = "pin"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _CODE_RE.finditer(text):
            if len(m.group()) == 4 and has_any_keyword(
                window(text, m.start(), m.end(), radius=25).lower(), _PIN_KEYWORDS
            ):
                candidates.append(Candidate(PIIType.PIN, m.group(), m.start(), m.end(), self.name, 0.85))
        return candidates
