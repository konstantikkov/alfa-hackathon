from __future__ import annotations

import re

from app.core.enums import PIIType
from app.core.interfaces import CandidateDetector
from app.core.models import Candidate
from app.entity_resolution.naming import (
    ROLE_FIRST,
    ROLE_PATRONYMIC,
    ROLE_SURNAME,
    NameComponent,
    alias_type_for,
    assign_roles,
    components_to_metadata,
)
from app.replacement.morphology import (
    classify_name_word,
    detect_case,
    normalize_to_nominative,
)

_WORD_RE = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?")
_GAP_RE = re.compile(r"^\s+$")
_MAX_RUN = 3

# "И. И. Иванову" / "И.И. Иванову" -- 1-2 initials, then a surname-parsing word.
_INITIALS_THEN_SURNAME_RE = re.compile(
    r"(?<![А-ЯЁа-яё.])((?:[А-ЯЁ]\.\s?){1,2})([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)"
)
# "Иванов И. И." / "Иванов И.И." -- exactly 2 initials after the surname (a single
# trailing initial is too easily a sentence-ending word: "... встретил Иванова И.").
_SURNAME_THEN_INITIALS_RE = re.compile(r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s((?:[А-ЯЁ]\.\s?){2})")
_INITIAL_RE = re.compile(r"[А-ЯЁ]\.")

# One signal among several (spec section 8) -- never sufficient on its own to KEEP.
_PUBLIC_FIGURE_SURNAMES = {
    "пушкин",
    "толстой",
    "чехов",
    "менделеев",
    "чайковский",
    "гагарин",
    "достоевский",
    "есенин",
    "лермонтов",
    "ломоносов",
    "циолковский",
    "королёв",
    "суворов",
    "кутузов",
    "жуков",
    "рахманинов",
    "шостакович",
}


class FullNameDetector(CandidateDetector):
    """Finds 2-3 word runs of consecutive capitalized tokens where pymorphy3's
    top-ranked parse of *each* token carries a Name/Surn/Patr grammeme -- this is
    what keeps a sentence-initial common noun like "Клиент" or "Банк" (no such
    grammeme) from being swept into the run, without needing a hardcoded name list.
    """

    name = "fio"

    def detect(self, text: str) -> list[Candidate]:
        tokens = self._name_tokens(text)
        candidates: list[Candidate] = []
        i = 0
        while i < len(tokens):
            run, j = self._collect_run(text, tokens, i)
            if len(run) >= 2:
                candidates.append(self._build_candidate(text, run))
                i = j
            else:
                i += 1

        candidates.extend(self._detect_initials_forms(text))
        return candidates

    @staticmethod
    def _name_tokens(text: str) -> list[tuple]:
        tokens = []
        for m in _WORD_RE.finditer(text):
            classified = classify_name_word(m.group())
            if classified:
                role, parse = classified
                tokens.append((m.start(), m.end(), m.group(), role, parse))
        return tokens

    @staticmethod
    def _collect_run(text: str, tokens: list[tuple], i: int) -> tuple[list[tuple], int]:
        """Extend a run of adjacent name-like tokens while only separator
        characters lie between them; returns the run and the next index."""
        run = [tokens[i]]
        j = i + 1
        while j < len(tokens) and len(run) < _MAX_RUN:
            prev_end = run[-1][1]
            gap_start = tokens[j][0]
            if prev_end != gap_start and not _GAP_RE.match(text[prev_end:gap_start]):
                break
            run.append(tokens[j])
            j += 1
        return run, j

    def _detect_initials_forms(self, text: str) -> list[Candidate]:
        """Surname+initials aliases: "И. И. Иванову", "Иванову И. И."; the full
        word must genuinely parse as a surname (Surn grammeme) -- that is what
        rejects "В. Новгород" or "ул. Ленина"-style look-alikes."""
        out: list[Candidate] = []
        for m in _INITIALS_THEN_SURNAME_RE.finditer(text):
            initials_str, surname = m.group(1), m.group(2)
            out.extend(
                self._initials_candidate(
                    text,
                    m.start(),
                    m.end(),
                    surname,
                    m.start(2),
                    initials_str,
                    m.start(1),
                    initials_first=True,
                )
            )
        for m in _SURNAME_THEN_INITIALS_RE.finditer(text):
            surname, initials_str = m.group(1), m.group(2)
            out.extend(
                self._initials_candidate(
                    text,
                    m.start(),
                    m.end(),
                    surname,
                    m.start(1),
                    initials_str,
                    m.start(2),
                    initials_first=False,
                )
            )
        return out

    def _initials_candidate(
        self,
        text: str,
        start: int,
        end: int,
        surname: str,
        surname_pos: int,
        initials_str: str,
        initials_pos: int,
        initials_first: bool,
    ) -> list[Candidate]:
        classified = classify_name_word(surname)
        if not classified or classified[0] != "Surn":
            return []
        _, parse = classified
        gender = parse.tag.gender if parse.tag.gender in ("masc", "femn") else None
        gram_case = parse.tag.case if parse.tag.case else (detect_case(surname) or "nomn")

        # trim a trailing space captured by the initials group
        while end > start and text[end - 1] == " ":
            end -= 1
        value = text[start:end]

        nominal_surname = normalize_to_nominative(surname, gender)
        components = [
            NameComponent(
                kind="word",
                surface=surname,
                offset_start=surname_pos - start,
                offset_end=surname_pos - start + len(surname),
                role=ROLE_SURNAME,
                normalized=nominal_surname.lower(),
            )
        ]
        initial_roles = (ROLE_FIRST, ROLE_PATRONYMIC)
        for k, im in enumerate(_INITIAL_RE.finditer(initials_str)):
            comp_start = initials_pos - start + im.start()
            if comp_start + len(im.group()) > len(value):
                continue
            components.append(
                NameComponent(
                    kind="initial",
                    surface=im.group(),
                    offset_start=comp_start,
                    offset_end=comp_start + len(im.group()),
                    role=initial_roles[k] if k < 2 else None,
                    letter=im.group()[0],
                )
            )
        components.sort(key=lambda c: c.offset_start)

        return [
            Candidate(
                type=PIIType.FULL_NAME,
                value=value,
                start=start,
                end=end,
                detector=self.name,
                detector_confidence=0.7,
                metadata={
                    "gram_case": gram_case,
                    "gender": gender or "masc",
                    "nominal_form": nominal_surname,
                    "identity_key": f"person:{nominal_surname.lower()}",
                    "known_public_figure": nominal_surname.lower() in _PUBLIC_FIGURE_SURNAMES,
                    "name_parts": components_to_metadata(components),
                    "alias_type": alias_type_for(components),
                },
            )
        ]

    @staticmethod
    def _run_gender(run: list) -> str:
        """Most reliable gender source first: patronymic, then first name, then any."""
        for wanted_role in ("Patr", "Name", None):
            for _, _, _, role, parse in run:
                if (wanted_role is None or role == wanted_role) and parse.tag.gender:
                    return parse.tag.gender
        return "masc"

    @staticmethod
    def _run_case(run: list) -> str:
        """Patronymic/surname carry case most reliably; bare first names are ambiguous."""
        for wanted_role in ("Patr", "Surn", "Name"):
            for _, _, _, role, parse in run:
                if role == wanted_role and parse.tag.case:
                    return parse.tag.case
        return "nomn"

    def _build_candidate(self, text: str, run: list) -> Candidate:
        start, end = run[0][0], run[-1][1]
        value = text[start:end]
        gender = self._run_gender(run)
        gram_case = self._run_case(run)

        nominal_words = [normalize_to_nominative(word, gender) for _, _, word, _, _ in run]
        nominal_form = " ".join(nominal_words)
        is_known_public = any(w.lower() in _PUBLIC_FIGURE_SURNAMES for w in nominal_words)

        roles = assign_roles([role for _, _, _, role, _ in run], len(run))
        components = [
            NameComponent(
                kind="word",
                surface=word,
                offset_start=w_start - start,
                offset_end=w_end - start,
                role=roles[k] if k < len(roles) else None,
                normalized=nominal_words[k].lower(),
            )
            for k, (w_start, w_end, word, _, _) in enumerate(run)
        ]

        return Candidate(
            type=PIIType.FULL_NAME,
            value=value,
            start=start,
            end=end,
            detector=self.name,
            detector_confidence=0.85 if len(run) == 3 else 0.75,
            metadata={
                "gram_case": gram_case,
                "gender": gender,
                "nominal_form": nominal_form,
                "identity_key": f"person:{nominal_form.lower()}",
                "known_public_figure": is_known_public,
                "name_parts": components_to_metadata(components),
                "alias_type": alias_type_for(components),
            },
        )
