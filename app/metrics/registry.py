from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Module-level constants for repeated literals (S1192).
_DIRECTION = "direction"

REQUEST_COUNT = Counter("pii_requests_total", "Total /process requests", [_DIRECTION, "outcome"])
REQUEST_LATENCY = Histogram("pii_request_latency_seconds", "End-to-end /process latency", [_DIRECTION])
TOKENS_PROCESSED = Counter(
    "pii_tokens_processed_total",
    "Approximate tokens processed (TPS = rate of this)",
    [_DIRECTION],
)
ENTITIES_DETECTED = Counter("pii_entities_detected_total", "Candidates found (mask + keep)", [])
ENTITIES_MASKED = Counter("pii_entities_masked_total", "Candidates that resulted in a MASK decision", [])
REDIS_LATENCY = Histogram("pii_redis_latency_seconds", "Mapping store call latency", ["op"])
ERRORS = Counter("pii_errors_total", "Unhandled errors by category", ["category"])

STORAGE_LATENCY = Histogram(
    "pii_storage_latency_seconds",
    "Per-backend mapping store call latency",
    ["backend", "op"],
)
STORAGE_ERRORS = Counter("pii_storage_errors_total", "Storage backend errors", ["backend"])
STORAGE_DEGRADED_EVENTS = Counter(
    "pii_storage_degraded_events_total",
    "Degraded-path events (cache miss on outage etc.)",
    ["kind"],
)

ADMISSION_REJECTED = Counter("pii_admission_rejected_total", "429 responses by reason", ["reason"])
RETRY_AFTER = Histogram(
    "pii_retry_after_seconds",
    "Retry-After values issued with 429s",
    buckets=(1, 2, 5, 10, 15, 30),
)
# multiprocess_mode: with several uvicorn workers (PROMETHEUS_MULTIPROC_DIR set)
# gauges must aggregate across workers -- livesum for additive quantities,
# livemax for the one-hot load state ("worst worker wins" is the honest view).
IN_FLIGHT = Gauge(
    "pii_in_flight_requests",
    "Requests currently being processed",
    multiprocess_mode="livesum",
)
BACKLOG_WORK = Gauge(
    "pii_outstanding_work_units",
    "Weighted in-flight work units",
    multiprocess_mode="livesum",
)
LOAD_STATE = Gauge(
    "pii_load_state",
    "1 for the current load state, 0 otherwise",
    ["state"],
    multiprocess_mode="livemax",
)
