"""Regression coverage for defects identified in the uploaded source archive."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.api.routes import _map_process_error
from app.config.consumers import ConsumerConfig, ConsumerRegistry, _parse_consumer
from app.core.enums import Decision, MaskingMode, PIIType
from app.core.errors import PayloadConflictError, StorageUnavailableError
from app.core.models import Candidate, EntityMapping, PrivacyDecision
from app.demasking.demasker import ModeDemasker
from app.detectors.dates import DateDetector
from app.replacement.engine import build_transform_result
from app.replacement.synthetic_values import COUNTRIES, SyntheticGenerator
from tests.failover.test_process_failover import PRIVATE_TEXT, make_service

_DEFAULT = "default"
_DISABLED = "disabled"


@pytest.mark.parametrize("mode", list(MaskingMode))
def test_demask_disabled_blocks_exact_and_modified_responses(mode):
    service, _, _ = make_service()
    config = ConsumerConfig(_DEFAULT, demask=False, masking_mode=mode)
    service._consumers = ConsumerRegistry(config, {})
    masked = service.process(PRIVATE_TEXT, _DISABLED).result
    assert masked != PRIVATE_TEXT
    assert service.process(masked, _DISABLED).result == masked
    assert service.process("Ответ: " + masked, _DISABLED).result == "Ответ: " + masked


def test_current_policy_can_revoke_demasking():
    service, _, _ = make_service()
    config = ConsumerConfig(_DEFAULT, masking_mode=MaskingMode.TOKEN)
    service._consumers = ConsumerRegistry(config, {})
    masked = service.process(PRIVATE_TEXT, "revoked").result
    service._consumers = ConsumerRegistry(replace(config, demask=False), {})
    assert service.process(masked, "revoked").result == masked


def test_losing_first_request_with_different_content_fails_closed(monkeypatch):
    service, auth, _ = make_service()
    service.process(PRIVATE_TEXT, "race")
    winner = auth.inner.get("race")
    monkeypatch.setattr(service._store, "get", Mock(side_effect=[None, winner]))
    with pytest.raises(PayloadConflictError):
        service.process("Телефон клиента +7 900 000-00-00", "race")
    assert _map_process_error(PayloadConflictError(), "safe-hash").status_code == 409


def test_losing_first_request_uses_persisted_policy(monkeypatch):
    service, auth, _ = make_service()
    masked = service.process(PRIVATE_TEXT, "race").result
    winner = auth.inner.get("race")
    config = ConsumerConfig(_DEFAULT, masking_mode=MaskingMode.TOKEN)
    service._consumers = ConsumerRegistry(config, {})
    monkeypatch.setattr(service._store, "get", Mock(side_effect=[None, winner]))
    assert service.process(PRIVATE_TEXT, "race").result == masked


def test_losing_first_request_without_winner_fails_closed(monkeypatch):
    service, _, _ = make_service()
    monkeypatch.setattr(service._store, "save_if_absent", Mock(return_value=False))
    with pytest.raises(StorageUnavailableError):
        service.process(PRIVATE_TEXT, "missing")


def test_exception_details_are_not_logged(monkeypatch):
    from app.api import routes

    logger = Mock()
    monkeypatch.setattr(routes, "logger", logger)
    error = ValueError("private@example.com")
    assert _map_process_error(error, "safe-hash").status_code == 500
    assert "private@example.com" not in str(logger.mock_calls)


def test_explicit_empty_mask_types_is_preserved():
    config = _parse_consumer("empty", {"mask_types": []})
    assert not config.should_mask_type(PIIType.EMAIL)
    assert _parse_consumer("all", {}).should_mask_type(PIIType.EMAIL)


@pytest.mark.parametrize("value,prefix", [("", 0), ("letters", 0), ("7", 1)])
def test_digit_generator_rejects_unmodifiable_inputs(value, prefix):
    with pytest.raises(ValueError, match="no mutable"):
        SyntheticGenerator(0).digits_like(value, prefix)


def test_repeated_random_digit_draw_has_bounded_fallback(monkeypatch):
    generator = SyntheticGenerator(0)
    monkeypatch.setattr(generator._rng, "randint", lambda *_: 1)
    assert generator.digits_like("111") == "112"


def test_token_demasking_does_not_replace_restored_content():
    mappings = [
        EntityMapping("EMAIL_1", PIIType.EMAIL, "literal <EMAIL_2>", "<EMAIL_1>", "", ""),
        EntityMapping("EMAIL_2", PIIType.EMAIL, "second@example.com", "<EMAIL_2>", "", ""),
    ]
    assert ModeDemasker().demask("<EMAIL_1> <EMAIL_2>", mappings, MaskingMode.TOKEN) == (
        "literal <EMAIL_2> second@example.com"
    )


def test_exhausted_synthetic_pool_falls_back_to_reversible_tokens():
    text = "; ".join(COUNTRIES)
    decisions = []
    offset = 0
    for value in COUNTRIES:
        start = text.index(value, offset)
        end = start + len(value)
        decisions.append(
            PrivacyDecision(Candidate(PIIType.CITIZENSHIP, value, start, end, "test"), Decision.MASK, 1.0)
        )
        offset = end
    result = build_transform_result(text, decisions, MaskingMode.SYNTHETIC, "pool")
    assert all(m.metadata.get("synthetic_fallback") == "token" for m in result.mappings)
    assert ModeDemasker().demask(result.text, result.mappings, MaskingMode.SYNTHETIC) == text


@pytest.mark.parametrize("value", ["31.02.2000", "2000-13-01", "29 февраля 2001"])
def test_impossible_dates_are_not_detected(value):
    assert DateDetector().detect("Дата рождения: " + value) == []


@pytest.mark.parametrize("value", ["29.02.2000", "2000-02-29", "29 февраля 2000"])
def test_valid_leap_dates_are_detected(value):
    assert DateDetector().detect("Дата рождения: " + value)[0].value == value


def test_redis_outage_does_not_replace_configured_authority(monkeypatch):
    from app.config.settings import Settings
    from app.main import _build_mapping_store
    from app.storage.redis_store import RedisMappingStore

    redis = Mock(spec=RedisMappingStore)
    redis.ping.side_effect = ConnectionError("offline")
    redis.get.side_effect = ConnectionError("offline")
    monkeypatch.setattr(RedisMappingStore, "from_url", Mock(return_value=redis))
    store = _build_mapping_store(Settings(redis_url="redis://localhost:6379/0", mongo_url=""))
    assert store._auth is redis
    with pytest.raises(StorageUnavailableError):
        store.get("existing")
