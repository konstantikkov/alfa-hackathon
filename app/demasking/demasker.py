from __future__ import annotations

import re

from app.core.enums import MaskingMode, PIIType
from app.core.interfaces import Demasker
from app.core.models import EntityMapping
from app.replacement.morphology import (
    CASES,
    inflect_name_words,
    inflect_word,
    split_name_words,
)

_NAME_TYPES = (PIIType.FULL_NAME, PIIType.CARDHOLDER)

# Name-part role keys used in more than two places (S1192).
_SURNAME = "surname"
_FIRST = "first"
_PATRONYMIC = "patronymic"
_GENDER = "gender"

# Shape symbol -> identity-dict role. Upper case = full word, lower = initial.
_ROLE_BY_SYMBOL = {
    "S": _SURNAME,
    "F": _FIRST,
    "P": _PATRONYMIC,
    "f": _FIRST,
    "p": _PATRONYMIC,
}

Span = tuple[int, int, str]


def _resolve_and_apply(text: str, spans: list[Span]) -> str:
    ordered = sorted(spans, key=lambda s: (-(s[1] - s[0]), s[0]))
    chosen: list[Span] = []
    occupied: list[tuple[int, int]] = []
    for start, end, replacement in ordered:
        if any(start < e and s < end for s, e in occupied):
            continue
        chosen.append((start, end, replacement))
        occupied.append((start, end))

    chosen.sort(key=lambda s: s[0], reverse=True)
    result = text
    for start, end, replacement in chosen:
        result = result[:start] + replacement + result[end:]
    return result


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _find_all_spans(text: str, needle_replacements: list[tuple[str, str]]) -> list[Span]:
    """Find every (needle -> replacement) match, one needle at a time via
    `str.find` rather than regex alternation.

    A combined `(?<!\\w)(alt1|alt2|...|altN)(?!\\w)` regex was tried first: it
    still tries every alternative at every text position (Python's backtracking
    engine has no Aho-Corasick-style multi-pattern fan-out), so it stayed
    O(len(text) * num_needles) -- measured 3.4s on a 100k-token document with ~90
    needles (35 entities x up to 6 grammatical cases each). `str.find` is
    CPython's optimized (Boyer-Moore-Horspool-ish) substring search in C, so doing
    it once per needle is far cheaper in practice than one interpreted regex
    alternative-scan per position; word-boundary checks become two O(1) character
    comparisons instead of a lookaround assertion.
    """
    items = [(n, r) for n, r in needle_replacements if n]
    spans: list[Span] = []
    text_len = len(text)

    for needle, replacement in items:
        needle_len = len(needle)
        search_from = 0
        while True:
            idx = text.find(needle, search_from)
            if idx == -1:
                break
            end = idx + needle_len
            before_ok = idx == 0 or not _is_word_char(text[idx - 1])
            after_ok = end == text_len or not _is_word_char(text[end])
            if before_ok and after_ok:
                spans.append((idx, end, replacement))
            search_from = idx + 1

    return spans


class ModeDemasker(Demasker):
    """Restores original values from token/synthetic surrogates found anywhere in
    arbitrary text -- it does NOT require the text to be byte-identical to what we
    originally returned, because a real LLM in the middle may have reworded around a
    surrogate. If a surrogate isn't found, it's left alone: we never guess.
    """

    def demask(self, text: str, mappings: list[EntityMapping], mode: MaskingMode) -> str:
        if mode == MaskingMode.TOKEN:
            return self._demask_token(text, mappings)
        if mode == MaskingMode.SYNTHETIC:
            return self._demask_synthetic(text, mappings)
        # PARTIAL destroys information (stars) -- not reconstructible without the
        # exact-match fast path handled by the caller (stored original_text).
        return text

    def _demask_token(self, text: str, mappings: list[EntityMapping]) -> str:
        replacements = {m.token: m.original for m in mappings if m.token}
        if not replacements:
            return text
        pattern = re.compile("|".join(re.escape(t) for t in sorted(replacements, key=len, reverse=True)))
        # Match only the input: restored originals may themselves contain tokens.
        return pattern.sub(lambda match: replacements[match.group()], text)

    def _demask_synthetic(self, text: str, mappings: list[EntityMapping]) -> str:
        needle_replacements: list[tuple[str, str]] = []
        for mapping in mappings:
            if mapping.token:
                # An occurrence may have been downgraded to TOKEN when synthetic
                # morphology was not confident; tokens never occur accidentally.
                needle_replacements.append((mapping.token, mapping.original))
            if mapping.type in _NAME_TYPES:
                needle_replacements.extend(self._name_needle_replacements(mapping))
            elif mapping.synthetic:
                needle_replacements.append((mapping.synthetic, mapping.original))

        spans = _find_all_spans(text, needle_replacements)
        return _resolve_and_apply(text, spans)

    def _name_needle_replacements(self, mapping: EntityMapping) -> list[tuple[str, str]]:
        identity = mapping.metadata.get("synthetic_identity") if mapping.metadata else None
        original_identity = mapping.metadata.get("original_identity") if mapping.metadata else None

        if not identity:
            if mapping.synthetic_nominal:
                return [
                    (
                        mapping.synthetic_nominal,
                        mapping.original_nominal or mapping.original,
                    )
                ]
            return []
        if not original_identity:
            return self._legacy_name_pairs(mapping, identity)
        return _alias_shape_pairs(identity, original_identity)

    def _legacy_name_pairs(self, mapping: EntityMapping, identity: dict) -> list[tuple[str, str]]:
        original_words = split_name_words(mapping.original_nominal or mapping.original)
        gender = mapping.gender or "masc"
        pairs: list[tuple[str, str]] = []
        synthetic_words = [
            identity[_SURNAME],
            identity[_FIRST],
            identity[_PATRONYMIC],
        ]
        for case in CASES:
            surface = inflect_name_words(synthetic_words, case, identity.get(_GENDER, gender))
            if surface:
                restored = inflect_name_words(original_words, case, gender)
                pairs.append((surface, restored))
        return pairs


# Alias shapes an LLM may echo back: component roles in surface order.
# S=surname, F=first, P=patronymic, f/p=initial of first/patronymic.
_WORD_SHAPES: list[tuple[str, ...]] = [
    ("S", "F", "P"),
    ("F", "P", "S"),
    ("F", "S"),
    ("S", "F"),
    ("F", "P"),
]
_INITIAL_SHAPES: list[tuple[str, ...]] = [
    ("f", "p", "S"),
    ("S", "f", "p"),
    ("f", "S"),
]


def _render_shape(
    shape: tuple[str, ...],
    parts: dict,
    case: str,
    gender: str,
    *,
    compact_initials: bool,
) -> str | None:
    """One surface for a shape; None when a required part is missing. Initials
    do not inflect; words do. compact_initials renders "А.М." vs "А. М."."""
    out: list[str] = []
    pending_initial = False
    for symbol in shape:
        word = parts.get(_ROLE_BY_SYMBOL[symbol])
        if not word:
            return None
        if symbol in ("f", "p"):
            token = word[0] + "."
            if pending_initial and compact_initials:
                out[-1] = out[-1] + token
            else:
                out.append(token)
            pending_initial = True
        else:
            out.append(inflect_word(word, case, gender))
            pending_initial = False
    return " ".join(out)


def _render_original_like(shape: tuple[str, ...], parts: dict, case: str, gender: str) -> str | None:
    """Original surface for the SAME shape, degrading gracefully when the
    original identity lacks a part (drop it rather than invent one)."""
    reduced = tuple(s for s in shape if parts.get(_ROLE_BY_SYMBOL[s]))
    if not reduced:
        return None
    return _render_shape(reduced, parts, case, gender, compact_initials=False)


def _collect_shape_pair(
    pairs: dict[str, str],
    shape: tuple[str, ...],
    identity: dict,
    original_identity: dict,
    case: str,
    genders: tuple[str, str],
    *,
    compact: bool,
) -> None:
    needle = _render_shape(shape, identity, case, genders[0], compact_initials=compact)
    if not needle:
        return
    restored = _render_original_like(shape, original_identity, case, genders[1])
    if restored:
        pairs.setdefault(needle, restored)


def _alias_shape_pairs(identity: dict, original_identity: dict) -> list[tuple[str, str]]:
    gender_syn = identity.get(_GENDER) or "masc"
    genders = (gender_syn, original_identity.get(_GENDER) or gender_syn)
    pairs: dict[str, str] = {}
    for case in CASES:
        for shape in _WORD_SHAPES:
            _collect_shape_pair(pairs, shape, identity, original_identity, case, genders, compact=False)
        for shape in _INITIAL_SHAPES:
            for compact in (False, True):
                _collect_shape_pair(pairs, shape, identity, original_identity, case, genders, compact=compact)
    return list(pairs.items())
