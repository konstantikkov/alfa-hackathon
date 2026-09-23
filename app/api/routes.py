from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import get_mapping_store, get_process_service
from app.api.schemas import HealthResponse, ProcessRequest, ProcessResponse
from app.config.logging import get_logger
from app.core.errors import (
    ConsumerDisabledError,
    ConsumerNotFoundError,
    PayloadConflictError,
    StorageUnavailableError,
)
from app.core.hashing import sha256_hex
from app.core.interfaces import MappingStore
from app.core.process_service import ProcessService
from app.core.text_stats import approx_token_count
from app.metrics.registry import (
    ERRORS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
    TOKENS_PROCESSED,
)

router = APIRouter()
logger = get_logger("app.api")

_BACKEND = "backend"


@router.get("/health", response_model=HealthResponse)
def health(request: Request, store: MappingStore = Depends(get_mapping_store)) -> HealthResponse:
    storage = store.health() if hasattr(store, "health") else {}
    auth = storage.get("authoritative", storage)
    cache = storage.get("cache")
    redis_status = "not-configured"
    if cache is not None:
        redis_status = "ok" if cache.get("ok") and cache.get("circuit") == "closed" else "degraded"
    elif auth.get(_BACKEND) == "redis":
        redis_status = "ok" if auth.get("ok") else "unreachable"
    elif auth.get(_BACKEND) in ("memory", "InMemoryMappingStore"):
        redis_status = "in-memory"

    load_state = "NORMAL"
    admission = getattr(request.app.state, "admission", None)
    if admission is not None:
        snapshot = admission.snapshot()
        load_state = snapshot["state"]
        storage = {**storage, "admission": snapshot}
    return HealthResponse(status="ok", redis=redis_status, storage=storage, load_state=load_state)


@router.get("/metrics")
def metrics() -> Response:
    # With multiple uvicorn workers each process keeps its own registry; the
    # default generate_latest() would expose whichever worker answered. In
    # multiprocess mode (PROMETHEUS_MULTIPROC_DIR) aggregate across workers.
    import os

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import CollectorRegistry, multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Admission control happens in app/admission/middleware.py (pure ASGI, on the
# event loop) -- a gate here, behind the sync-endpoint threadpool, cannot
# reject the requests already queued in front of it.


def _map_process_error(exc: Exception, payload_id_hash: str) -> HTTPException:
    """Translate pipeline/storage errors to HTTP responses (PII never logged)."""
    if isinstance(exc, ConsumerNotFoundError):
        ERRORS.labels(category="consumer_not_found").inc()
        return HTTPException(status_code=403, detail="unknown consumer")
    if isinstance(exc, ConsumerDisabledError):
        ERRORS.labels(category="consumer_disabled").inc()
        return HTTPException(status_code=403, detail="consumer disabled")
    if isinstance(exc, PayloadConflictError):
        ERRORS.labels(category="payload_conflict").inc()
        return HTTPException(status_code=409, detail="payload_id conflict; use a new payload_id")
    if isinstance(exc, StorageUnavailableError):
        # Fail closed: the authoritative store cannot guarantee a reversible
        # mapping (or safely answer whether one exists). 503 + a hint to retry.
        ERRORS.labels(category="storage_unavailable").inc()
        logger.error("storage_unavailable", payload_id_hash=payload_id_hash)
        return HTTPException(
            status_code=503,
            detail="mapping storage unavailable",
            headers={"Retry-After": "5"},
        )
    ERRORS.labels(category="internal").inc()
    logger.error("process_failed", payload_id_hash=payload_id_hash, error_type=type(exc).__name__)
    return HTTPException(status_code=500, detail="internal error")


@router.post("/process", response_model=ProcessResponse)
def process(
    body: ProcessRequest,
    request: Request,
    service: ProcessService = Depends(get_process_service),
) -> ProcessResponse:
    payload_id_hash = sha256_hex(body.payload_id)[:16]
    started = time.perf_counter()
    direction = "unknown"

    try:
        outcome = service.process(body.payload, body.payload_id)
        direction = outcome.direction.value
        REQUEST_COUNT.labels(direction=direction, outcome="success").inc()
        return ProcessResponse(result=outcome.result)
    except Exception as exc:
        REQUEST_COUNT.labels(direction=direction, outcome="error").inc()
        raise _map_process_error(exc, payload_id_hash) from exc
    finally:
        latency = time.perf_counter() - started
        REQUEST_LATENCY.labels(direction=direction).observe(latency)
        estimated_tokens = approx_token_count(body.payload)
        TOKENS_PROCESSED.labels(direction=direction).inc(estimated_tokens)
        logger.info(
            "process_request",
            payload_id_hash=payload_id_hash,
            direction=direction,
            request_size=len(body.payload),
            estimated_tokens=estimated_tokens,
            latency_ms=round(latency * 1000, 2),
        )
