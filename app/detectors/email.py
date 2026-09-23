from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate

# Bounded quantifiers everywhere (RFC 5321-ish limits: local part <=64, each label
# <=63, <=8 subdomain levels) -- NOT just style. An unbounded `[A-Za-z0-9._%+-]*`
# before a required-but-possibly-absent `@` is a textbook ReDoS: on a long run of
# alnum characters with no `@` anywhere (e.g. a long numeric ID or hex string), the
# engine retries the same expensive greedy scan from every single start position,
# which is O(n^2) -- measured at 67s for a 200KB digit string before this fix.
# Bounding the quantifier caps the work done per start position at a small constant.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)


class EmailDetector(CandidateDetector):
    name = "email"

    def detect(self, text: str) -> list[Candidate]:
        if "@" not in text:  # memchr-fast prescan; most documents have no email
            return []
        return [
            Candidate(PIIType.EMAIL, m.group(), m.start(), m.end(), self.name, 0.95)
            for m in _EMAIL_RE.finditer(text)
        ]
