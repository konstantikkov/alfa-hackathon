from __future__ import annotations

import re
from datetime import date

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.core.ru_text import GENITIVE_MONTHS
from app.detectors.keyword_gate import first_matching_keyword, window

_NUMERIC_RE = re.compile(r"(?<!\d)(\d{1,2})([./\-])(\d{1,2})\2(\d{4})(?!\d)")
_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_MONTHS = GENITIVE_MONTHS
_TEXT_RE = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})(?!\d)", re.IGNORECASE)

_BIRTH_KEYWORDS = ("дата рождения", "рожден", "родился", "родилась")
_ISSUE_KEYWORDS = ("дата выдачи", "выдан", "выдана", "выдано")


def _calendar_fields(pattern: re.Pattern[str], m: re.Match[str]) -> tuple[int, int, int]:
    if pattern is _ISO_RE:
        year, month, day = map(int, m.groups())
    elif pattern is _TEXT_RE:
        day_s, month_name, year_s = m.groups()
        year, month, day = int(year_s), _MONTHS.index(month_name.lower()) + 1, int(day_s)
    else:
        day_s, _, month_s, year_s = m.groups()
        year, month, day = int(year_s), int(month_s), int(day_s)
    return year, month, day


def _is_real_date(pattern: re.Pattern[str], m: re.Match[str]) -> bool:
    """31.02.2000 matches the digit shape but is not a date -- masking it would
    corrupt what is actually arbitrary digits."""
    year, month, day = _calendar_fields(pattern, m)
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


class DateDetector(CandidateDetector):
    """A date's role (birth / passport-issue / ordinary business date) determines
    whether it's personal at all -- an ordinary date has no privacy meaning, so if
    neither role keyword is nearby we don't emit a candidate (spec section 7).
    """

    name = "date"

    def detect(self, text: str) -> list[Candidate]:
        candidates = []
        for pattern in (_NUMERIC_RE, _ISO_RE, _TEXT_RE):
            for m in pattern.finditer(text):
                if not _is_real_date(pattern, m):
                    continue
                role = self._classify(text, m.start(), m.end())
                if role is not None:
                    candidates.append(Candidate(role, m.group(), m.start(), m.end(), self.name, 0.85))
        return candidates

    def _classify(self, text: str, start: int, end: int) -> PIIType | None:
        local = window(text, start, end, radius=30).lower()
        if first_matching_keyword(local, _BIRTH_KEYWORDS):
            return PIIType.BIRTH_DATE
        if first_matching_keyword(local, _ISSUE_KEYWORDS):
            return PIIType.PASSPORT_ISSUE_DATE
        return None
