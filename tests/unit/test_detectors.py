from app.core.enums import PIIType
from app.detectors.card import CardDetector
from app.detectors.cardholder import CardholderDetector
from app.detectors.checksums import inn_valid, luhn_valid
from app.detectors.cvv_pin import CvvDetector, PinDetector
from app.detectors.dates import DateDetector
from app.detectors.department_code import DepartmentCodeDetector
from app.detectors.driver_license import DriverLicenseDetector
from app.detectors.email import EmailDetector
from app.detectors.inn import InnDetector
from app.detectors.passport import PassportDetector
from app.detectors.phone import PhoneDetector


def _values(candidates):
    return [c.value for c in candidates]


def test_luhn_valid_known_number():
    assert luhn_valid("0" * 16) is True  # zero checksum completes zero digits
    assert luhn_valid("0" * 15 + "1") is False
    # Non-zero digits exercise the doubling rule: exactly one check digit
    # completes any base -- derived at runtime, no number literal needed.
    base = "123456789012345"
    completions = [d for d in "0123456789" if luhn_valid(base + d)]
    assert len(completions) == 1


def test_inn_valid_10_and_12_digit():
    # Zero check digits are checksum-correct for all-zero bodies.
    assert inn_valid("0" * 10) is True
    assert inn_valid("0" * 9 + "1") is False
    assert inn_valid("0" * 12) is True
    assert inn_valid("0" * 11 + "1") is False


def test_email_detector_finds_address():
    text = "Пишите на test.user@example.com по любым вопросам."
    found = EmailDetector().detect(text)
    assert _values(found) == ["test.user@example.com"]


def test_email_detector_does_not_redos_on_long_at_free_run():
    # Regression: an unbounded local-part quantifier before a required-but-absent
    # "@" is catastrophic backtracking -- 200k chars used to take 60+ seconds.
    import time

    text = "1" * 200_000
    started = time.perf_counter()
    found = EmailDetector().detect(text)
    elapsed = time.perf_counter() - started

    assert found == []
    assert elapsed < 2.0, f"email detection took {elapsed:.2f}s on an adversarial input (ReDoS regression)"


def test_phone_detector_formats():
    samples = [
        "+7 (900) 000-00-00",
        "+7 900 000 00 00",
        "89000000000",
        "+79000000000",
    ]
    for phone in samples:
        text = f"Телефон: {phone}."
        found = PhoneDetector().detect(text)
        assert found, f"failed to detect {phone!r}"
        assert found[0].value == phone


def test_inn_detector_requires_valid_checksum():
    text = f"ИНН клиента: {'0' * 10}."
    found = InnDetector().detect(text)
    assert len(found) == 1
    assert found[0].type == PIIType.INN

    text_bad = f"ИНН клиента: {'0' * 9}1."
    assert InnDetector().detect(text_bad) == []


def test_card_detector_luhn_and_separators():
    from tests.support import dummy_card

    number = dummy_card()
    spaced = " ".join(number[i : i + 4] for i in range(0, 16, 4))
    text = f"Карта {spaced} привязана к счету."
    found = CardDetector().detect(text)
    assert len(found) == 1
    assert found[0].metadata["normalized"] == number


def test_card_detector_rejects_invalid_luhn():
    text = "Идентификатор 1234 5678 9012 3456 в отчете."
    assert CardDetector().detect(text) == []


def test_passport_series_and_bare_forms():
    text1 = "Паспорт серия 0000 номер 000000 выдан отделом."
    found1 = PassportDetector().detect(text1)
    assert len(found1) == 1

    text2 = "Клиент предъявил паспорт 0000 000000 для проверки."
    found2 = PassportDetector().detect(text2)
    assert len(found2) == 1

    text3 = "Внутренний идентификатор 0000 000000 в системе."
    assert PassportDetector().detect(text3) == []


def test_department_code_requires_keyword():
    text_masked = "Код подразделения 000-000 указан в паспорте."
    assert len(DepartmentCodeDetector().detect(text_masked)) == 1

    text_negative = "Внутренний код документа: 000-000."
    assert DepartmentCodeDetector().detect(text_negative) == []


def test_cvv_requires_card_context():
    assert len(CvvDetector().detect("Карта клиента, CVV 000.")) == 1
    assert CvvDetector().detect("Код подтверждения заказа: 000.") == []


def test_pin_requires_pin_keyword():
    assert len(PinDetector().detect("ПИН-код карты: 0000.")) == 1
    assert PinDetector().detect("Код подтверждения заказа: 0000.") == []


def test_driver_license_requires_keyword():
    text = "Водительское удостоверение клиента: 0000 000000."
    assert len(DriverLicenseDetector().detect(text)) == 1
    assert DriverLicenseDetector().detect("Просто число 0000 000000 без контекста.") == []


def test_birth_date_vs_ordinary_date():
    birth = DateDetector().detect("Дата рождения 12.05.1995 указана в анкете.")
    assert len(birth) == 1
    assert birth[0].type == PIIType.BIRTH_DATE

    issue = DateDetector().detect("Дата выдачи 01.02.2020 паспорта.")
    assert len(issue) == 1
    assert issue[0].type == PIIType.PASSPORT_ISSUE_DATE

    ordinary = DateDetector().detect("Заявка была создана 12.06.2026.")
    assert ordinary == []


def test_date_text_form():
    found = DateDetector().detect("Дата рождения 12 мая 1995 указана в анкете.")
    assert len(found) == 1
    assert found[0].value == "12 мая 1995"


def test_cardholder_detector():
    text = "держатель ИВАН ПЕТРОВ карты."
    found = CardholderDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "ИВАН ПЕТРОВ"
