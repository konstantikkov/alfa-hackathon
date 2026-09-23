from __future__ import annotations


def window(text: str, start: int, end: int, radius: int = 40) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def has_any_keyword(haystack_lower: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in haystack_lower for kw in keywords)


def first_matching_keyword(haystack_lower: str, keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        if kw in haystack_lower:
            return kw
    return None
