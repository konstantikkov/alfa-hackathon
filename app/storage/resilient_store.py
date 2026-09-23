from __future__ import annotations

import threading
import time

from app.config.logging import get_logger
from app.core.errors import StorageUnavailableError
from app.core.interfaces import MappingStore
from app.metrics.registry import (
    STORAGE_DEGRADED_EVENTS,
    STORAGE_ERRORS,
    STORAGE_LATENCY,
)

# Module-level constants for repeated literals (S1192).
_CLOSED = "closed"
_UPDATE = "update"

logger = get_logger("app.storage")


class CircuitBreaker:
    """Failure-counting breaker: after `failure_threshold` consecutive failures the
    circuit opens for `cooldown_seconds`; after the cooldown any single call may
    probe (half-open) and a success closes it again."""

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: float = 10.0) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._failures < self._threshold:
                return _CLOSED
            opened_at = self._opened_at if self._opened_at is not None else time.monotonic()
            return "half_open" if time.monotonic() - opened_at >= self._cooldown else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold and self._opened_at is None:
                self._opened_at = time.monotonic()
                logger.warning("circuit_opened", breaker=self.name)
            elif self._failures >= self._threshold:
                # re-open after a failed half-open probe
                self._opened_at = time.monotonic()


class ResilientMappingStore(MappingStore):
    """MongoDB-authoritative store with a Redis hot cache in front.

    Semantics (per failure-mode matrix):
      authoritative OK + cache OK    -> normal
      authoritative OK + cache FAIL  -> success, CACHE_DEGRADED
      authoritative FAIL + cache hit -> reads (demask of existing mapping) succeed
      authoritative FAIL, new write  -> StorageUnavailableError (fail closed)
      authoritative FAIL + cache miss-> StorageUnavailableError on read: we cannot
                                        distinguish "no record" from "record exists
                                        but unreachable", and guessing would turn a
                                        demask request into a re-mask.
    """

    def __init__(
        self,
        authoritative: MappingStore,
        cache: MappingStore | None = None,
        *,
        authoritative_name: str = "authoritative",
        cache_name: str = "cache",
        failure_threshold: int = 3,
        cooldown_seconds: float = 10.0,
        backfill_ttl_seconds: int = 300,
    ) -> None:
        self._auth = authoritative
        self._cache = cache
        self._auth_breaker = CircuitBreaker(authoritative_name, failure_threshold, cooldown_seconds)
        self._cache_breaker = CircuitBreaker(cache_name, failure_threshold, cooldown_seconds)
        self._auth_name = authoritative_name
        self._cache_name = cache_name
        self._backfill_ttl = backfill_ttl_seconds
        # labels() is a locked lookup; memoize the (backend, op) series.
        self._latency_series: dict[tuple[str, str], object] = {}

    # --- instrumented low-level calls -------------------------------------------

    def _latency(self, backend: str, op: str):
        key = (backend, op)
        series = self._latency_series.get(key)
        if series is None:
            series = self._latency_series[key] = STORAGE_LATENCY.labels(backend=backend, op=op)
        return series

    def _auth_call(self, op: str, fn, *args):
        if not self._auth_breaker.allow():
            raise StorageUnavailableError(f"{self._auth_name} circuit open")
        start = time.perf_counter()
        try:
            result = fn(*args)
        except Exception as exc:
            STORAGE_ERRORS.labels(backend=self._auth_name).inc()
            self._auth_breaker.record_failure()
            raise StorageUnavailableError(f"{self._auth_name} {op} failed") from exc
        finally:
            self._latency(self._auth_name, op).observe(time.perf_counter() - start)
        self._auth_breaker.record_success()
        return result

    def _cache_call(self, op: str, fn, *args):
        """Best-effort: cache failures degrade, never break the request."""
        if self._cache is None or not self._cache_breaker.allow():
            return None, False
        start = time.perf_counter()
        try:
            result = fn(*args)
        except Exception:
            STORAGE_ERRORS.labels(backend=self._cache_name).inc()
            STORAGE_DEGRADED_EVENTS.labels(kind="cache_error").inc()
            self._cache_breaker.record_failure()
            return None, False
        finally:
            self._latency(self._cache_name, op).observe(time.perf_counter() - start)
        self._cache_breaker.record_success()
        return result, True

    # --- MappingStore ------------------------------------------------------------

    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        created = self._auth_call(
            "save_if_absent", self._auth.save_if_absent, payload_id, record, ttl_seconds
        )
        if created:
            self._cache_call(
                _UPDATE,
                self._cache.update if self._cache else None,
                payload_id,
                record,
                ttl_seconds,
            )
        return bool(created)

    def get(self, payload_id: str) -> dict | None:
        cached, cache_ok = self._cache_call("get", self._cache.get if self._cache else None, payload_id)
        if cached is not None:
            return cached
        try:
            record = self._auth_call("get", self._auth.get, payload_id)
        except StorageUnavailableError:
            # A cache hit above would already have returned; a miss (or cache
            # outage) with the authority down cannot be answered safely.
            raise
        if record is not None and self._cache is not None and cache_ok:
            # backfill only after a genuine cache miss (cache is reachable)
            self._cache_call(_UPDATE, self._cache.update, payload_id, record, self._backfill_ttl)
        return record

    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        self._auth_call(_UPDATE, self._auth.update, payload_id, record, ttl_seconds)
        self._cache_call(
            _UPDATE,
            self._cache.update if self._cache else None,
            payload_id,
            record,
            ttl_seconds,
        )

    def delete(self, payload_id: str) -> None:
        self._cache_call("delete", self._cache.delete if self._cache else None, payload_id)
        self._auth_call("delete", self._auth.delete, payload_id)

    def mark_demasked(self, payload_id: str, grace_seconds: int) -> None:
        """Lifecycle transition ACTIVE -> DEMASKED_GRACE. Tolerates a down
        authority (the demask itself already succeeded from cache); the mapping
        then simply lives until its original TTL instead of the shorter grace.

        The authority gets a targeted state update (MongoMappingRepository
        overrides mark_demasked with a $set); the cache is refreshed from its
        own copy only -- no extra authoritative read on this hot path."""
        from app.core.enums import MappingState

        try:
            self._auth_call("mark_demasked", self._auth.mark_demasked, payload_id, grace_seconds)
        except StorageUnavailableError:
            STORAGE_DEGRADED_EVENTS.labels(kind="grace_transition_not_durable").inc()

        if self._cache is not None:
            cached, cache_ok = self._cache_call("get", self._cache.get, payload_id)
            if cached is not None and cache_ok:
                cached["state"] = MappingState.DEMASKED_GRACE.value
                cached["demasked_at"] = time.time()
                self._cache_call(_UPDATE, self._cache.update, payload_id, cached, grace_seconds)

    # --- introspection for /health and admission ---------------------------------

    def health(self) -> dict:
        auth_health = self._auth.health() if hasattr(self._auth, "health") else {"ok": True}
        result = {
            "authoritative": {
                "backend": self._auth_name,
                "circuit": self._auth_breaker.state,
                **auth_health,
            }
        }
        if self._cache is not None:
            cache_health = self._cache.health() if hasattr(self._cache, "health") else {"ok": True}
            result["cache"] = {
                "backend": self._cache_name,
                "circuit": self._cache_breaker.state,
                **cache_health,
            }
        return result

    @property
    def authoritative_degraded(self) -> bool:
        return self._auth_breaker.state != _CLOSED

    @property
    def cache_degraded(self) -> bool:
        return self._cache is not None and self._cache_breaker.state != _CLOSED
