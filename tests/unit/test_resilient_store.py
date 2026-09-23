from __future__ import annotations

import pytest

from app.core.enums import MappingState
from app.core.errors import StorageUnavailableError
from app.storage.resilient_store import CircuitBreaker, ResilientMappingStore
from tests.support import FlakyStore

# Module-level constants for repeated literals (S1192).
_CLOSED = "closed"
_STATE = "state"


def make_store(with_cache: bool = True):
    auth = FlakyStore()
    cache = FlakyStore() if with_cache else None
    store = ResilientMappingStore(
        auth,
        cache,
        authoritative_name="mongo",
        cache_name="redis",
        failure_threshold=3,
        cooldown_seconds=0.05,
    )
    return store, auth, cache


def test_normal_write_populates_both_stores():
    store, auth, cache = make_store()
    assert store.save_if_absent("p1", {"v": 1}, 60) is True
    assert auth.inner.get("p1") == {"v": 1}
    assert cache.inner.get("p1") == {"v": 1}


def test_cache_down_write_succeeds_degraded():
    store, auth, cache = make_store()
    cache.down = True
    assert store.save_if_absent("p1", {"v": 1}, 60) is True
    assert auth.inner.get("p1") == {"v": 1}
    assert cache.inner.get("p1") is None
    assert store.cache_degraded is False  # single failure below threshold
    cache.down = False
    # reads fall back to the authority and backfill the cache once it works again
    assert store.get("p1") == {"v": 1}
    assert cache.inner.get("p1") == {"v": 1}


def test_authoritative_down_new_write_fails_closed():
    store, auth, _cache = make_store()
    auth.down = True
    with pytest.raises(StorageUnavailableError):
        store.save_if_absent("p1", {"v": 1}, 60)


def test_authoritative_down_cache_hit_still_serves_reads():
    store, auth, _cache = make_store()
    store.save_if_absent("p1", {"v": 1}, 60)
    auth.down = True
    assert store.get("p1") == {"v": 1}


def test_authoritative_down_cache_miss_read_fails_closed():
    store, auth, _cache = make_store()
    auth.down = True
    with pytest.raises(StorageUnavailableError):
        store.get("missing")


def test_both_down_controlled_failure():
    store, auth, cache = make_store()
    auth.down = True
    cache.down = True
    with pytest.raises(StorageUnavailableError):
        store.get("p1")
    with pytest.raises(StorageUnavailableError):
        store.save_if_absent("p1", {"v": 1}, 60)


def test_redis_miss_mongo_hit_backfills_cache():
    store, auth, cache = make_store()
    auth.inner.save_if_absent("p1", {"v": 1}, 60)  # present only in the authority
    assert store.get("p1") == {"v": 1}
    assert cache.inner.get("p1") == {"v": 1}


def test_no_backfill_while_cache_is_down():
    store, auth, cache = make_store()
    auth.inner.save_if_absent("p1", {"v": 1}, 60)
    cache.down = True
    assert store.get("p1") == {"v": 1}
    assert cache.inner.get("p1") is None


def test_breaker_opens_after_threshold_and_recovers():
    import time

    store, auth, _cache = make_store(with_cache=False)
    auth.down = True
    for _ in range(3):
        with pytest.raises(StorageUnavailableError):
            store.get("x")
    assert store.authoritative_degraded is True
    # while open, calls are rejected without touching the backend
    calls_before = len(auth.calls)
    with pytest.raises(StorageUnavailableError):
        store.get("x")
    assert len(auth.calls) == calls_before
    # after cooldown a half-open probe reaches the recovered backend
    auth.down = False
    time.sleep(0.06)
    assert store.get("x") is None
    assert store.authoritative_degraded is False


def test_mark_demasked_moves_to_grace():
    store, auth, cache = make_store()
    store.save_if_absent("p1", {_STATE: MappingState.ACTIVE.value, "v": 1}, 600)
    store.mark_demasked("p1", 30)
    rec = auth.inner.get("p1")
    assert rec[_STATE] == MappingState.DEMASKED_GRACE.value
    assert rec["demasked_at"] is not None
    assert cache.inner.get("p1")[_STATE] == MappingState.DEMASKED_GRACE.value


def test_mark_demasked_tolerates_down_authority():
    store, auth, cache = make_store()
    store.save_if_absent("p1", {_STATE: MappingState.ACTIVE.value}, 600)
    auth.down = True
    store.mark_demasked("p1", 30)  # must not raise: demask itself already succeeded
    assert cache.inner.get("p1")[_STATE] == MappingState.DEMASKED_GRACE.value


def test_circuit_breaker_states():
    b = CircuitBreaker("t", failure_threshold=2, cooldown_seconds=10)
    assert b.state == _CLOSED
    b.record_failure()
    assert b.state == _CLOSED
    b.record_failure()
    assert b.state == "open"
    b.record_success()
    assert b.state == _CLOSED
