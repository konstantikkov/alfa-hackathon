from __future__ import annotations

import pytest

from app.admission.controller import AdmissionController
from app.core.enums import LoadState
from app.core.errors import AdmissionRejectedError


def make(max_in_flight=2, max_backlog=10_000, **kw):
    return AdmissionController(
        max_in_flight=max_in_flight,
        max_backlog_work_units=max_backlog,
        base_cost=100,
        per_char_cost=1.0,
        min_retry_after=1.0,
        max_retry_after=30.0,
        **kw,
    )


def test_accepts_within_limits_and_releases():
    adm = make()
    t1 = adm.try_acquire(500)
    assert adm.snapshot()["in_flight"] == 1
    assert adm.snapshot()["outstanding_work"] == 600
    adm.release(t1)
    assert adm.snapshot()["in_flight"] == 0
    assert adm.snapshot()["outstanding_work"] == 0


def test_rejects_over_in_flight_limit_with_retry_after():
    adm = make(max_in_flight=2)
    adm.try_acquire(10)
    adm.try_acquire(10)
    with pytest.raises(AdmissionRejectedError) as exc_info:
        adm.try_acquire(10)
    exc = exc_info.value
    assert exc.reason == "in_flight_limit"
    assert exc.load_state == LoadState.OVERLOADED.value
    assert 1.0 <= exc.retry_after <= 30.0


def test_rejects_over_backlog_limit():
    adm = make(max_in_flight=100, max_backlog=1_000)
    adm.try_acquire(500)  # work = 600
    with pytest.raises(AdmissionRejectedError) as exc_info:
        adm.try_acquire(500)  # would be 1200 > 1000
    assert exc_info.value.reason == "backlog_limit"


def test_retry_after_grows_with_backlog():
    adm = make(max_in_flight=3, max_backlog=100_000)
    # simulate observed service rate: acquire+release a few to prime EWMAs
    for _ in range(5):
        t = adm.try_acquire(1000)
        adm.release(t)
    small_backlog_retry = adm._retry_after_locked(100)

    tickets = [adm.try_acquire(20_000) for _ in range(3)]
    big_backlog_retry = adm._retry_after_locked(100)
    assert big_backlog_retry >= small_backlog_retry
    for t in tickets:
        adm.release(t)


def test_retry_after_clamped_to_bounds():
    adm = make()
    assert 1.0 <= adm._retry_after_locked(10_000_000) <= 30.0


def test_storage_degraded_state_and_modifier():
    class DegradedStore:
        authoritative_degraded = True
        cache_degraded = False

    adm = make(store=DegradedStore())
    assert adm.load_state() == LoadState.STORAGE_DEGRADED


def test_cache_degraded_state():
    class Store:
        authoritative_degraded = False
        cache_degraded = True

    adm = make(store=Store())
    assert adm.load_state() == LoadState.CACHE_DEGRADED


def test_critical_on_rss_limit():
    adm = make(rss_limit_mb=0.001)  # any real process exceeds this
    with pytest.raises(AdmissionRejectedError) as exc_info:
        adm.try_acquire(10)
    assert exc_info.value.load_state == LoadState.CRITICAL.value


def test_api_returns_429_with_retry_after_header():
    import os

    os.environ.setdefault("PII_REDIS_URL", "")
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        app.state.admission = AdmissionController(
            max_in_flight=0, max_backlog_work_units=1, min_retry_after=2.0
        )
        resp = client.post("/process", json={"payload": "привет", "payload_id": "adm-1"})
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 2
        health = client.get("/health").json()
        assert health["load_state"] in ("OVERLOADED", "NORMAL", "CRITICAL")


def test_oversized_payload_gets_413():
    import os

    os.environ.setdefault("PII_REDIS_URL", "")
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        big = "х" * 2_000_000  # 2M chars -> ~4MB UTF-8, over the 3MB cap
        resp = client.post("/process", json={"payload": big, "payload_id": "big-413"})
        assert resp.status_code == 413
