from app.context.engine import ContextualPrivacyEngine
from app.core.enums import Decision, PIIType
from app.core.models import Candidate

# Module-level constants for repeated literals (S1192).
_ALEKSANDR_SERGEEVICH_PUSHKIN = "Александр Сергеевич Пушкин"

engine = ContextualPrivacyEngine()


def _candidate(pii_type, value, start, end, **metadata):
    return Candidate(
        type=pii_type,
        value=value,
        start=start,
        end=end,
        detector="test",
        metadata=metadata,
    )


def test_public_person_reference_is_kept():
    text = "Александр Сергеевич Пушкин написал «Руслан и Людмила»"
    start = text.index("Александр")
    end = start + len(_ALEKSANDR_SERGEEVICH_PUSHKIN)
    candidate = _candidate(PIIType.FULL_NAME, _ALEKSANDR_SERGEEVICH_PUSHKIN, start, end)

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.KEEP


def test_public_person_in_private_context_is_masked():
    text = "Поэт Александр Сергеевич Пушкин, паспорт 0000 000000, хочет взять кредит"
    start = text.index("Александр")
    end = start + len(_ALEKSANDR_SERGEEVICH_PUSHKIN)
    candidate = _candidate(PIIType.FULL_NAME, _ALEKSANDR_SERGEEVICH_PUSHKIN, start, end)

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.MASK


def test_bank_client_name_is_masked():
    text = "Клиент Иванов Иван Иванович обратился в банк за ипотечным кредитом"
    start = text.index("Иванов")
    end = start + len("Иванов Иван Иванович")
    candidate = _candidate(PIIType.FULL_NAME, "Иванов Иван Иванович", start, end)

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.MASK


def test_bank_office_address_is_kept():
    text = "Отделение банка находится по адресу Москва, Тверская, 10"
    value = "Москва, Тверская, 10"
    start = text.index(value)
    candidate = _candidate(PIIType.ADDRESS, value, start, start + len(value))

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.KEEP


def test_client_registered_address_is_masked():
    text = "Клиент зарегистрирован по адресу Москва, Тверская, 10"
    value = "Москва, Тверская, 10"
    start = text.index(value)
    candidate = _candidate(PIIType.ADDRESS, value, start, start + len(value))

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.MASK


def test_structured_type_defaults_to_mask():
    value = "0" * 10
    text = f"ИНН клиента: {value}."
    start = text.index(value)
    candidate = _candidate(PIIType.INN, value, start, start + len(value))

    decision = engine.decide(text, candidate, [candidate])

    assert decision.decision == Decision.MASK
