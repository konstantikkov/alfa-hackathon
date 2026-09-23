import os
import threading

# Module-level constants for repeated literals (S1192).
_INSPECT = "/inspect"
_PAYLOAD = "payload"
_PAYLOAD_ID = "payload_id"
_PROCESS = "/process"
_RESULT = "result"

os.environ.setdefault("PII_REDIS_URL", "")

# The env var above must be set BEFORE app.main is imported (settings are read
# at import), so these imports legitimately follow executable code.
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def make_client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_health_endpoint():
    with make_client() as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_mask_then_demask_round_trip():
    with make_client() as client:
        payload = "Клиент Иванов Иван Иванович, телефон +7 900 000-00-00."
        payload_id = "rt-1"

        r1 = client.post(_PROCESS, json={_PAYLOAD: payload, _PAYLOAD_ID: payload_id})
        assert r1.status_code == 200
        masked = r1.json()[_RESULT]
        assert masked != payload

        r2 = client.post(_PROCESS, json={_PAYLOAD: masked, _PAYLOAD_ID: payload_id})
        assert r2.status_code == 200
        assert r2.json()[_RESULT] == payload


def test_retry_of_first_request_returns_same_masked_result_not_demasked():
    with make_client() as client:
        payload = "Клиент Петров Петр Петрович обратился в банк."
        payload_id = "retry-1"

        r1 = client.post(_PROCESS, json={_PAYLOAD: payload, _PAYLOAD_ID: payload_id})
        r1_retry = client.post(_PROCESS, json={_PAYLOAD: payload, _PAYLOAD_ID: payload_id})

        assert r1.json()[_RESULT] == r1_retry.json()[_RESULT]
        assert r1_retry.json()[_RESULT] != payload


def test_no_pii_payload_round_trips_as_identity():
    with make_client() as client:
        payload = "Общая информация без персональных данных."
        payload_id = "no-pii-1"

        r1 = client.post(_PROCESS, json={_PAYLOAD: payload, _PAYLOAD_ID: payload_id})
        assert r1.json()[_RESULT] == payload


def test_unknown_payload_id_field_missing_returns_422():
    with make_client() as client:
        r = client.post(_PROCESS, json={_PAYLOAD: "x"})
        assert r.status_code == 422


def test_ui_index_served_at_root():
    with make_client() as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_inspect_returns_full_pipeline_breakdown():
    with make_client() as client:
        payload = "Клиент Иванов Иван Иванович, паспорт 0000 000000."
        r = client.post(_INSPECT, json={_PAYLOAD: payload, "mode": "synthetic"})
        assert r.status_code == 200
        data = r.json()
        assert {c["type"] for c in data["candidates"]} == {"FULL_NAME", "PASSPORT"}
        assert all(d["decision"] == "MASK" for d in data["decisions"])
        assert data["masked_text"] != payload
        assert data["round_trip_ok"] is True
        assert data["demasked_text"] == payload


def test_inspect_partial_mode_round_trip_is_not_applicable():
    with make_client() as client:
        payload = "Клиент Петров Петр Петрович обратился в банк."
        r = client.post(_INSPECT, json={_PAYLOAD: payload, "mode": "partial"})
        assert r.status_code == 200
        assert r.json()["round_trip_ok"] is None


def test_inspect_rejects_invalid_mode():
    with make_client() as client:
        r = client.post(_INSPECT, json={_PAYLOAD: "x", "mode": "not-a-mode"})
        assert r.status_code == 422


def test_concurrent_first_requests_same_payload_id_agree_on_result():
    """Two simultaneous "first" requests for a brand-new payload_id must not race:
    both should get back the identical masked text, and there must be exactly one
    winning record (checked indirectly: every response matches every other).
    """
    with make_client() as client:
        payload = f"Клиент Сидоров Сидор Сидорович, ИНН {'0' * 10}, обратился за кредитом."
        payload_id = "race-1"
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def call():
            try:
                barrier.wait(timeout=5)
                r = client.post(_PROCESS, json={_PAYLOAD: payload, _PAYLOAD_ID: payload_id})
                results.append(r.json()[_RESULT])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert len(results) == 8
        assert len(set(results)) == 1, f"race produced divergent results: {set(results)}"
        assert results[0] != payload
