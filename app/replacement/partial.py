from __future__ import annotations

import re
from collections.abc import Callable

from app.core.enums import PIIType


def mask_alnum(value: str) -> str:
    return "".join("*" if ch.isalnum() else ch for ch in value)


def mask_name(value: str) -> str:
    parts = [p for p in re.split(r"\s+", value.strip()) if p]
    return " ".join((next((c for c in p if c.isalpha()), "*").upper() + ".") for p in parts)


def mask_email(value: str) -> str:
    if "@" not in value:
        return mask_alnum(value)
    local, domain = value.rsplit("@", 1)
    return mask_alnum(local) + "@" + mask_alnum(domain)


def mask_phone(value: str) -> str:
    positions = [i for i, c in enumerate(value) if c.isdigit()]
    keep = {positions[0]} if positions else set()
    chars = list(value)
    for i in positions:
        if i not in keep:
            chars[i] = "*"
    return "".join(chars)


def mask_passport(value: str) -> str:
    positions = [i for i, c in enumerate(value) if c.isdigit()]
    keep = set(positions[:2] + positions[-2:])
    chars = list(value)
    for i in positions:
        if i not in keep:
            chars[i] = "*"
    return "".join(chars)


def mask_card(value: str) -> str:
    positions = [i for i, c in enumerate(value) if c.isdigit()]
    keep = set(positions[:4] + positions[-4:])
    chars = list(value)
    for i in positions:
        if i not in keep:
            chars[i] = "*"
    return "".join(chars)


def mask_numeric(value: str, first: int = 2, last: int = 2) -> str:
    positions = [i for i, c in enumerate(value) if c.isdigit()]
    keep = set(positions[:first] + positions[-last:]) if len(positions) > first + last else set()
    chars = list(value)
    for i in positions:
        if i not in keep:
            chars[i] = "*"
    return "".join(chars)


_PARTIAL_MASKERS: dict[PIIType, Callable[[str], str]] = {
    PIIType.FULL_NAME: mask_name,
    PIIType.CARDHOLDER: mask_name,
    PIIType.EMAIL: mask_email,
    PIIType.PHONE: mask_phone,
    PIIType.PASSPORT: mask_passport,
    PIIType.CARD_NUMBER: mask_card,
    PIIType.DRIVER_LICENSE: lambda v: mask_numeric(v, 2, 2),
    PIIType.INN: lambda v: mask_numeric(v, 2, 2),
}


def partial_mask(pii_type: PIIType, value: str) -> str:
    masker = _PARTIAL_MASKERS.get(pii_type, mask_alnum)
    return masker(value)
