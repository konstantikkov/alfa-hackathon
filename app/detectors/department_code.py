from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.detectors.keyword_gate import has_any_keyword, window

_CODE_RE = re.compile(r"(?<!\d)\d{3}-\d{3}(?!\d)")
_KEYWORDS = ("код подразделения", "подразделени")


class DepartmentCodeDetector(CandidateDetector):
    """Format alone is not sufficient (spec section 7) -- an arbitrary XXX-XXX code
    elsewhere in a document (order id, internal doc id) must not be masked.
    """

    name = "department_code"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for m in _CODE_RE.finditer(text):
            if has_any_keyword(window(text, m.start(), m.end()).lower(), _KEYWORDS):
                candidates.append(
                    Candidate(
                        PIIType.DEPARTMENT_CODE,
                        m.group(),
                        m.start(),
                        m.end(),
                        self.name,
                        0.92,
                    )
                )
        return candidates
