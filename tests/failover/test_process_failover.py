"""Failure-semantics tests for the full ProcessService path over the resilient
Mongo-authoritative + Redis-cache store, with injected outages.

Matrix covered here (see also tests/unit/test_resilient_store.py for the
store-level cases): normal mask/demask, Redis down (mask + demask via Mongo),
Redis restored, Mongo down + new mask (fail closed), Mongo down + cached demask
(succeeds), both down (controlled failure), retry during DEMASKED_GRACE, grace
expiry purge, no-PII request without heavy mapping, 100k payload not persisted.
"""

from __future__ import annotations

import json

import pytest

from app.config.consumers import ConsumerRegistry
from app.context.engine import ContextualPrivacyEngine
from app.core.enums import MappingState, ProcessDirection
from app.core.errors import StorageUnavailableError
from app.core.pipeline import PiiPipeline
from app.core.process_service import ProcessService
from app.demasking.demasker import ModeDemasker
from app.detectors.builtin import register_builtin_detectors
from app.detectors.registry import DetectorRegistry
from app.policies.engine import PolicyEngine
from app.storage.resilient_store import ResilientMappingStore
from tests.support import FlakyStore

# Module-level constants for repeated literals (S1192).
_PID_1 = "pid-1"
_PID_NOPII = "pid-nopii"

PRIVATE_TEXT = "Клиент Иванов Иван Иванович обратился за кредитом, паспорт 0000 000000."
NO_PII_TEXT = "Сегодня хорошая погода, отделение работает до 18:00."


def make_service(mapping_ttl: int = 600, grace: int = 60):
    detectors = DetectorRegistry()
    register_builtin_detectors(detectors)
    pipeline = PiiPipeline(detectors, ContextualPrivacyEngine(), PolicyEngine())
    consumers = ConsumerRegistry.load("app/config/consumers.yaml")
    auth = FlakyStore()
    cache = FlakyStore()
    store = ResilientMappingStore(
        auth,
        cache,
        authoritative_name="mongo",
        cache_name="redis",
        failure_threshold=3,
        cooldown_seconds=0.05,
    )
    service = ProcessService(
        pipeline=pipeline,
        demasker=ModeDemasker(),
        store=store,
        consumer_registry=consumers,
        mapping_ttl_seconds=mapping_ttl,
        demask_retry_grace_seconds=grace,
    )
    return service, auth, cache


def test_normal_mask_then_demask_round_trip():
    service, _auth, _cache = make_service()
    masked = service.process(PRIVATE_TEXT, _PID_1)
    assert masked.direction == ProcessDirection.MASK
    assert "Иванов Иван Иванович" not in masked.result
    demasked = service.process(masked.result, _PID_1)
    assert demasked.direction == ProcessDirection.DEMASK
    assert demasked.result == PRIVATE_TEXT


def test_retry_mask_returns_identical_result_without_new_mapping():
    service, _auth, _cache = make_service()
    first = service.process(PRIVATE_TEXT, _PID_1)
    retry = service.process(PRIVATE_TEXT, _PID_1)
    assert retry.direction == ProcessDirection.RETRY_MASK
    assert retry.result == first.result


def test_redis_down_mask_and_demask_work_through_mongo():
    service, _auth, cache = make_service()
    cache.down = True
    masked = service.process(PRIVATE_TEXT, _PID_1)
    demasked = service.process(masked.result, _PID_1)
    assert demasked.result == PRIVATE_TEXT
    assert cache.inner.get(_PID_1) is None  # nothing ever reached the cache


def test_redis_restored_after_outage_backfills_on_read():
    service, _auth, cache = make_service()
    cache.down = True
    masked = service.process(PRIVATE_TEXT, _PID_1)
    cache.down = False
    service.process(PRIVATE_TEXT, _PID_1)  # retry-mask reads through and backfills
    assert cache.inner.get(_PID_1) is not None
    demasked = service.process(masked.result, _PID_1)
    assert demasked.result == PRIVATE_TEXT


def test_mongo_down_new_mask_fails_closed():
    service, auth, _cache = make_service()
    auth.down = True
    with pytest.raises(StorageUnavailableError):
        service.process(PRIVATE_TEXT, _PID_1)


def test_mongo_down_no_pii_request_still_succeeds():
    service, auth, _cache = make_service()
    auth.down = True
    outcome = service.process(NO_PII_TEXT, _PID_NOPII)
    assert outcome.result == NO_PII_TEXT  # nothing reversible was needed


def test_mongo_down_existing_mapping_in_redis_demask_succeeds():
    service, auth, _cache = make_service()
    masked = service.process(PRIVATE_TEXT, _PID_1)
    auth.down = True
    demasked = service.process(masked.result, _PID_1)
    assert demasked.result == PRIVATE_TEXT


def test_both_down_controlled_failure():
    service, auth, cache = make_service()
    masked = service.process(PRIVATE_TEXT, _PID_1)
    auth.down = True
    cache.down = True
    with pytest.raises(StorageUnavailableError):
        service.process(masked.result, _PID_1)


def test_demask_retry_during_grace_is_idempotent():
    service, auth, _cache = make_service()
    masked = service.process(PRIVATE_TEXT, _PID_1)
    first = service.process(masked.result, _PID_1)
    record = auth.inner.get(_PID_1)
    assert record["state"] == MappingState.DEMASKED_GRACE.value
    retry = service.process(masked.result, _PID_1)  # response was "lost", client retries
    assert retry.result == first.result == PRIVATE_TEXT


def test_mapping_purged_after_grace_expiry():
    service, auth, cache = make_service(grace=-1)  # grace already expired on write
    masked = service.process(PRIVATE_TEXT, _PID_1)
    service.process(masked.result, _PID_1)
    assert auth.inner.get(_PID_1) is None
    assert cache.inner.get(_PID_1) is None


def test_no_pii_request_creates_minimal_record():
    service, auth, _cache = make_service()
    outcome = service.process(NO_PII_TEXT, _PID_NOPII)
    assert outcome.result == NO_PII_TEXT
    record = auth.inner.get(_PID_NOPII)
    assert record["no_pii"] is True
    assert record["mappings"] == []
    assert record["occurrences"] == []
    assert NO_PII_TEXT not in json.dumps(record, ensure_ascii=False)


def test_large_payload_not_persisted():
    service, auth, _cache = make_service()
    filler = "Обычное предложение о погоде и природе без имен. " * 4000
    payload = f"Клиент Иванов Иван Иванович обратился за кредитом. {filler}"
    assert len(payload) > 190_000
    service.process(payload, "pid-large")
    record = auth.inner.get("pid-large")
    serialized = json.dumps(record, ensure_ascii=False)
    # The record stores fingerprints + mappings, never the document itself.
    assert len(serialized) < 20_000
    assert "погоде и природе" not in serialized


def test_concurrent_first_requests_agree():
    import threading

    service, _auth, _cache = make_service()
    barrier = threading.Barrier(8)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        outcome = service.process(PRIVATE_TEXT, "pid-race")
        with lock:
            results.append(outcome.result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    demasked = service.process(results[0], "pid-race")
    assert demasked.result == PRIVATE_TEXT
