from __future__ import annotations

from app.core.enums import PIIType

TOKEN_PREFIX: dict[PIIType, str] = {
    PIIType.FULL_NAME: "PERSON",
    PIIType.BIRTH_DATE: "BIRTH_DATE",
    PIIType.BIRTH_PLACE: "BIRTH_PLACE",
    PIIType.PASSPORT: "PASSPORT",
    PIIType.CITIZENSHIP: "CITIZENSHIP",
    PIIType.PASSPORT_ISSUER: "PASSPORT_ISSUER",
    PIIType.DEPARTMENT_CODE: "DEPARTMENT_CODE",
    PIIType.PASSPORT_ISSUE_DATE: "PASSPORT_ISSUE_DATE",
    PIIType.DRIVER_LICENSE: "DRIVER_LICENSE",
    PIIType.ADDRESS: "ADDRESS",
    PIIType.EMAIL: "EMAIL",
    PIIType.PHONE: "PHONE",
    PIIType.INN: "INN",
    PIIType.CARD_NUMBER: "CARD",
    PIIType.CVV: "CVV",
    PIIType.PIN: "PIN",
    PIIType.CARDHOLDER: "CARDHOLDER",
}


def token_for(entity_id: str) -> str:
    return f"<{entity_id}>"
