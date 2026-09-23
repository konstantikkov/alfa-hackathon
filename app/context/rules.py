from __future__ import annotations

import re

# Keyword lists per the track spec (section 8). These are explainable, editable signals --
# not an exhaustive dictionary. Extend as benchmark error analysis surfaces gaps.

PERSONAL_ROLES = (
    "клиент",
    "клиента",
    "клиенту",
    "клиентом",
    "клиенте",
    "заемщик",
    "заёмщик",
    "заемщика",
    "заёмщика",
    "заемщику",
    "заёмщику",
    "заявитель",
    "заявителя",
    "заявителю",
    "страхователь",
    "страхователя",
    "страхователю",
    "застрахованный",
    "застрахованного",
    "застрахованному",
    "держатель",
    "держателя",
    "держателю",
    "получатель",
    "получателя",
    "получателю",
    "созаемщик",
    "созаёмщик",
    "созаемщика",
    "созаёмщика",
    "пользователь",
    "пользователя",
    "пользователю",
)

PRIVATE_ACTIONS = (
    "обратился",
    "обратилась",
    "обратились",
    "подал заявку",
    "подала заявку",
    "подали заявку",
    "оформил",
    "оформила",
    "оформили",
    "зарегистрирован",
    "зарегистрирована",
    "зарегистрированы",
    "проживает",
    "проживают",
    "проживал",
    "проживала",
    "сообщил",
    "сообщила",
    "сообщили",
    "указал",
    "указала",
    "указали",
)

BANK_PRIVATE_CONCEPTS = (
    "кредит",
    "ипотек",
    "договор",
    "полис",
    "счет",
    "счёт",
    "заявление",
)

STRONG_RELATED_PII_KEYWORDS = (
    "паспорт",
    "телефон",
    "email",
    "e-mail",
    "почта",
    "инн",
    "карт",
    "cvv",
    "пин",
    "дата рождения",
    "адрес проживания",
    "адрес регистрации",
)

PUBLIC_MARKERS = (
    "поэт",
    "писатель",
    "автор",
    "ученый",
    "учёный",
    "композитор",
    "актер",
    "актёр",
    "историческая личность",
    "написал",
    "написала",
    "создал",
    "создала",
    "произведение",
    "биография",
    "роман",
    "стихотворение",
    "космонавт",
    "изобрел",
    "изобрёл",
    "открыл",
    "основал",
)

ORGANIZATIONAL_ADDRESS_MARKERS = (
    "офис",
    "отделени",
    "филиал",
    "банкомат",
    "магазин",
    "штаб-квартир",
    "представительств",
    "точка продаж",
    "точке продаж",
)

PERSONAL_ADDRESS_MARKERS = (
    "проживает",
    "проживал",
    "проживала",
    "зарегистрирован",
    "зарегистрирована",
    "адрес клиента",
    "адрес проживания",
    "адрес регистрации",
    "домашний адрес",
    "место жительства",
    "прописан",
    "прописана",
)

_WORD_RE = re.compile(r"[а-яёa-z0-9\-]+", re.IGNORECASE)


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "-_"


def _tail_is_short(window_lower: str, end: int, max_tail: int = 2) -> bool:
    """True when at most `max_tail` word characters follow position `end`."""
    j = end
    tail = 0
    while j < len(window_lower) and _is_word_char(window_lower[j]):
        j += 1
        tail += 1
        if tail > max_tail:
            return False
    return True


def _keyword_hit(window_lower: str, keyword: str) -> bool:
    """Word-start match with a bounded (<=2 chars) inflection tail.

    Raw substring matching produced real false signals on the golden set:
    "роман" fired inside the patronymic "Романович" (KEEP for a client!), and
    "карт"/"пин" fired inside "картинах"/"Репина" (MASK for a public painter).
    Word-start kills mid-word hits; the 2-char tail still accepts Russian case
    endings ("карту", "картой", "романы") while rejecting derivational stems
    ("картинах", "Романович").
    """
    start = 0
    while True:
        idx = window_lower.find(keyword, start)
        if idx == -1:
            return False
        starts_word = idx == 0 or not _is_word_char(window_lower[idx - 1])
        if starts_word and _tail_is_short(window_lower, idx + len(keyword)):
            return True
        start = idx + 1


def count_hits(window_lower: str, keywords: tuple[str, ...]) -> list[str]:
    return [kw for kw in keywords if _keyword_hit(window_lower, kw)]


def sentence_bounds(text: str, start: int, end: int, radius_chars: int = 220) -> tuple[int, int]:
    """Bounded local context: the sentence containing the span, else a char radius.

    Never scans the whole document -- required for 100k-token inputs where a single
    PERSON candidate must not trigger a full-document pass.
    """
    left_bound = max(0, start - radius_chars)
    right_bound = min(len(text), end + radius_chars)
    local = text[left_bound:right_bound]

    rel_start = start - left_bound
    rel_end = end - left_bound

    sentence_start = 0
    for match in re.finditer(r"[.!?\n]", local[:rel_start]):
        sentence_start = match.end()
    sentence_end_match = re.search(r"[.!?\n]", local[rel_end:])
    sentence_end = rel_end + sentence_end_match.start() + 1 if sentence_end_match else len(local)

    return left_bound + sentence_start, left_bound + sentence_end


def sentence_window(text: str, start: int, end: int, radius_chars: int = 220) -> str:
    left, right = sentence_bounds(text, start, end, radius_chars)
    return text[left:right]


def attributed_window(
    text: str,
    start: int,
    end: int,
    other_spans: list[tuple[int, int]],
    radius_chars: int = 220,
) -> str:
    """Sentence window, further clipped so it never crosses into a *different*
    candidate's own span.

    Without this, a sentence with two names -- "Клиент Иванов ... читает романы
    автора Пушкина." -- would let "Клиент" (which belongs to Иванов) leak into
    Пушкин's context and wrongly flip a public reference to MASK.
    """
    left, right = sentence_bounds(text, start, end, radius_chars)
    for other_start, other_end in other_spans:
        if other_end <= start:
            left = max(left, other_end)
        elif other_start >= end:
            right = min(right, other_start)
    return text[left:right]
