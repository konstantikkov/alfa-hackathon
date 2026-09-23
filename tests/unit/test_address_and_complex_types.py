from app.detectors.address import AddressDetector
from app.detectors.birth_place import BirthPlaceDetector
from app.detectors.citizenship import CitizenshipDetector
from app.detectors.passport_issuer import PassportIssuerDetector


def test_address_with_index():
    text = "Адрес регистрации клиента: 123456, г. Москва, ул. Тверская, д. 10, кв. 5."
    found = AddressDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "123456, г. Москва, ул. Тверская, д. 10, кв. 5"


def test_address_word_form():
    text = "Адрес регистрации клиента: г. Казань, ул. Победы, дом 3, квартира 12."
    found = AddressDetector().detect(text)
    assert len(found) == 1


def test_address_compact_form():
    text = "Адрес регистрации клиента: Самара, Мира, 7-42."
    found = AddressDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "Самара, Мира, 7-42"


def test_citizenship_detector():
    text = "Место рождения клиента: г. Тула; гражданство: Российская Федерация."
    found = CitizenshipDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "Российская Федерация"


def test_birth_place_detector_variants():
    for phrase, expected in (
        ("Место рождения клиента: г. Тула; гражданство: Россия.", "г. Тула"),
        ("Место рождения клиента: город Псков; гражданство: Россия.", "город Псков"),
        (
            "Место рождения клиента: г. Санкт-Петербург; гражданство: Беларусь.",
            "г. Санкт-Петербург",
        ),
    ):
        found = BirthPlaceDetector().detect(phrase)
        assert len(found) == 1
        assert found[0].value == expected


def test_passport_issuer_detector():
    text = "Паспорт 4509 123456, выдан ГУ МВД России по г. Москва, код подразделения 770-001."
    found = PassportIssuerDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "ГУ МВД России по г. Москва"


def test_passport_issuer_hyphenated_city():
    text = "Паспорт 4509 123456, выдан УМВД России по г. Санкт-Петербург, код подразделения 770-001."
    found = PassportIssuerDetector().detect(text)
    assert len(found) == 1
    assert found[0].value == "УМВД России по г. Санкт-Петербург"
