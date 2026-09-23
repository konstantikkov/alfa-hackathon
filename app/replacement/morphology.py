from __future__ import annotations

import functools

import pymorphy3

CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct")


@functools.lru_cache(maxsize=1)
def get_analyzer() -> pymorphy3.MorphAnalyzer:
    # Loading the dictionaries is the expensive part (~150ms) -- do it exactly once
    # per worker process, not per request.
    return pymorphy3.MorphAnalyzer()


# Profiling on the short-text benchmark showed pymorphy3 lookups are ~67% of
# pipeline CPU (dawg traversal per word), while the word vocabulary repeats
# heavily across requests (names, role words, capitalized sentence starters).
# Memoizing these pure functions is the single biggest throughput lever; the
# caches are per-process and bounded.
@functools.lru_cache(maxsize=65536)
def guess_gender(first_name: str) -> str:
    analyzer = get_analyzer()
    for parse in analyzer.parse(first_name):
        if "Name" in parse.tag and parse.tag.gender in ("masc", "femn"):
            return parse.tag.gender
    for parse in analyzer.parse(first_name):
        if parse.tag.gender in ("masc", "femn"):
            return parse.tag.gender
    return "masc"


@functools.lru_cache(maxsize=65536)
def detect_case(word: str) -> str | None:
    analyzer = get_analyzer()
    for parse in analyzer.parse(word):
        if parse.tag.case in CASES:
            return parse.tag.case
    return None


@functools.lru_cache(maxsize=65536)
def inflect_word(word: str, case: str, gender: str | None = None) -> str:
    """Best-effort inflection of a single word into `case`, keeping capitalization."""
    if case == "nomn" or case not in CASES:
        return word

    analyzer = get_analyzer()
    parses = analyzer.parse(word)
    candidates = parses
    if gender:
        gendered = [p for p in parses if p.tag.gender == gender]
        if gendered:
            candidates = gendered

    for parse in candidates:
        # Force singular: a name inside a FULL_NAME run is always singular, but
        # pymorphy's frequency model sometimes ranks a plural reading of a name
        # higher than the singular one (e.g. "Алины" as nominative-plural outranks
        # genitive-singular of "Алина"), which would silently pluralize a name.
        inflected = parse.inflect({case, "sing"})
        if inflected:
            result = inflected.word
            return result.capitalize() if word[:1].isupper() else result

    return word


def inflect_full_name(surname: str, first: str, patronymic: str, case: str, gender: str) -> str:
    return inflect_name_words([surname, first, patronymic], case, gender)


def inflect_name_words(words: list[str], case: str, gender: str) -> str:
    return " ".join(inflect_word(w, case, gender) for w in words if w)


def split_name_words(nominal: str) -> list[str]:
    return [w for w in nominal.strip().split() if w]


_NAME_ROLES = ("Patr", "Surn", "Name")


@functools.lru_cache(maxsize=65536)
def classify_name_word(word: str):
    """Best (role, parse) among Name/Surn/Patr grammemes within `word`'s top-2
    ranked parses -- e.g. "Льва" (genitive of the name "Лев") is genuinely
    outscored by "льва" (genitive of "лев", lion) at rank 1 but appears at rank 2;
    going further down the list starts pulling in common nouns' obscure homonym
    readings and would cause false positives.

    Returned Parse objects are treated as read-only by all callers (cached).
    """
    analyzer = get_analyzer()
    for parse in analyzer.parse(word)[:2]:
        for role in _NAME_ROLES:
            if role in parse.tag:
                return role, parse
    return None


@functools.lru_cache(maxsize=65536)
def normalize_to_nominative(word: str, gender: str | None = None) -> str:
    analyzer = get_analyzer()
    parses = analyzer.parse(word)
    candidates = parses
    if gender:
        gendered = [p for p in parses if p.tag.gender == gender]
        if gendered:
            candidates = gendered
    for parse in candidates:
        inflected = parse.inflect({"nomn", "sing"})
        if inflected:
            result = inflected.word
            return result.capitalize() if word[:1].isupper() else result
    return word


def detect_name_case(surname: str, first: str, patronymic: str | None) -> str:
    """Vote across the available parts; a bare given name is often case-ambiguous
    (e.g. genitive/accusative collide for many first names), a surname or
    patronymic is more reliable.
    """
    for part in (patronymic, surname, first):
        if not part:
            continue
        case = detect_case(part)
        if case:
            return case
    return "nomn"
