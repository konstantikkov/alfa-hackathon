from __future__ import annotations

from enum import StrEnum


class PIIType(StrEnum):
    FULL_NAME = "FULL_NAME"
    BIRTH_DATE = "BIRTH_DATE"
    BIRTH_PLACE = "BIRTH_PLACE"
    PASSPORT = "PASSPORT"
    CITIZENSHIP = "CITIZENSHIP"
    PASSPORT_ISSUER = "PASSPORT_ISSUER"
    DEPARTMENT_CODE = "DEPARTMENT_CODE"
    PASSPORT_ISSUE_DATE = "PASSPORT_ISSUE_DATE"
    DRIVER_LICENSE = "DRIVER_LICENSE"
    ADDRESS = "ADDRESS"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    INN = "INN"
    CARD_NUMBER = "CARD_NUMBER"
    CVV = "CVV"
    PIN = "PIN"
    CARDHOLDER = "CARDHOLDER"


class Decision(StrEnum):
    MASK = "MASK"
    KEEP = "KEEP"
    UNCERTAIN = "UNCERTAIN"


class MaskingMode(StrEnum):
    PARTIAL = "partial"
    TOKEN = "token"  # NOSONAR -- masking mode name, not a credential
    SYNTHETIC = "synthetic"


class ProcessDirection(StrEnum):
    MASK = "mask"
    DEMASK = "demask"
    RETRY_MASK = "retry_mask"
    RETRY_DEMASK = "retry_demask"


class MappingState(StrEnum):
    ACTIVE = "ACTIVE"
    DEMASKED_GRACE = "DEMASKED_GRACE"


class LoadState(StrEnum):
    NORMAL = "NORMAL"
    CACHE_DEGRADED = "CACHE_DEGRADED"
    STORAGE_DEGRADED = "STORAGE_DEGRADED"
    OVERLOADED = "OVERLOADED"
    CRITICAL = "CRITICAL"
