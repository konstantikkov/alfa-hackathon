from app.detectors.fio import FullNameDetector

# Module-level constants for repeated literals (S1192).
_IVANOV_IVAN_IVANOVICH = "Иванов Иван Иванович"
_NOMINAL_FORM = "nominal_form"

detector = FullNameDetector()


def test_detects_full_triple_nominative():
    text = "Клиент Иванов Иван Иванович обратился в банк."
    found = detector.detect(text)
    assert len(found) == 1
    c = found[0]
    assert c.value == _IVANOV_IVAN_IVANOVICH
    assert c.metadata["gram_case"] == "nomn"
    assert c.metadata["gender"] == "masc"
    assert c.metadata[_NOMINAL_FORM] == _IVANOV_IVAN_IVANOVICH


def test_role_word_is_not_swept_into_the_name():
    text = "Клиент Иванов Иван Иванович обратился в банк."
    found = detector.detect(text)
    assert "Клиент" not in found[0].value


def test_detects_inflected_dative_form_and_normalizes_to_nominative():
    text = "Банк направил письмо Иванову Ивану Ивановичу."
    found = detector.detect(text)
    assert len(found) == 1
    c = found[0]
    assert c.value == "Иванову Ивану Ивановичу"
    assert c.metadata["gram_case"] == "datv"
    assert c.metadata[_NOMINAL_FORM] == _IVANOV_IVAN_IVANOVICH


def test_female_name_gender_detection():
    text = "Клиентка Смирнова Мария Ивановна оформила заявку."
    found = detector.detect(text)
    assert len(found) == 1
    assert found[0].metadata["gender"] == "femn"


def test_two_word_name_without_patronymic():
    text = "Заявку подал Иванов Иван."
    found = detector.detect(text)
    assert len(found) == 1
    assert found[0].value == "Иванов Иван"


def test_known_public_figure_flag_set():
    text = "Александр Сергеевич Пушкин написал поэму."
    found = detector.detect(text)
    assert len(found) == 1
    assert found[0].metadata["known_public_figure"] is True


def test_repeated_mentions_share_identity_key():
    text = "С Ивановым Иваном Ивановичем обсудили заявку. Позже письмо отправили Иванову Ивану Ивановичу."
    found = detector.detect(text)
    assert len(found) == 2
    assert found[0].metadata["identity_key"] == found[1].metadata["identity_key"]


def test_first_name_normalizes_to_singular_nominative_not_plural():
    # Regression: "Алины" (genitive singular of "Алина") ranks below "Алины" as
    # nominative *plural* in pymorphy's frequency model -- normalization must not
    # silently pluralize the name.
    text = "Заявление поступило от Морозовой Алины Сергеевны."
    found = detector.detect(text)
    assert len(found) == 1
    assert found[0].metadata[_NOMINAL_FORM] == "Морозова Алина Сергеевна"


def test_does_not_fire_on_plain_sentence_without_names():
    text = "Отделение банка находится по адресу Москва, Тверская, 10."
    found = detector.detect(text)
    assert found == []
