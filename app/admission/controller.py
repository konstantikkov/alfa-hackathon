"""Internal admission control / backpressure for the PII service.

Not a separate queue service: a bounded in-process gate in front of the
pipeline. Every request costs `work_units` (weighted by payload size); the
controller tracks in-flight work, EWMA arrival/service rates and storage
health, and rejects with a dynamically computed Retry-After before a queue can
build up.

    retry_after = (outstanding_work / service_work_rate)
                  * max(1, arrival_rate / service_rate)   # arrival pressure
                  * safety_factor
                  * storage_modifier
    clamped to [min_retry_after, max_retry_after]
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from app.core.enums import LoadState
from app.core.errors import AdmissionRejectedError

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _process_rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * _PAGE_SIZE / (1024 * 1024)
    except Exception:
        return 0.0


class _EwmaRate:
    """Events (or work units) per second, exponentially smoothed."""

    def __init__(self, alpha: float = 0.2) -> None:
        self._alpha = alpha
        self._rate = 0.0
        self._last: float | None = None

    def observe(self, amount: float = 1.0) -> None:
        now = time.monotonic()
        if self._last is not None:
            dt = max(now - self._last, 1e-3)
            instant = amount / dt
            self._rate = self._alpha * instant + (1 - self._alpha) * self._rate
        self._last = now

    @property
    def rate(self) -> float:
        return self._rate


@dataclass
class AdmissionTicket:
    work_units: float
    started: float


class AdmissionController:
    def __init__(
        self,
        *,
        max_in_flight: int = 64,
        max_backlog_work_units: float = 4_000_000,
        base_cost: float = 500.0,
        per_char_cost: float = 1.0,
        min_retry_after: float = 1.0,
        max_retry_after: float = 30.0,
        safety_factor: float = 1.5,
        rss_limit_mb: float = 12_288,
        ewma_alpha: float = 0.2,
        store=None,  # ResilientMappingStore, for degraded-state signals
    ) -> None:
        self._max_in_flight = max_in_flight
        self._max_backlog = max_backlog_work_units
        self._base_cost = base_cost
        self._per_char = per_char_cost
        self._min_retry = min_retry_after
        self._max_retry = max_retry_after
        self._safety = safety_factor
        self._rss_limit_mb = rss_limit_mb
        self._store = store

        self._lock = threading.Lock()
        self._in_flight = 0
        self._outstanding_work = 0.0
        self._arrivals = _EwmaRate(ewma_alpha)
        self._completions = _EwmaRate(ewma_alpha)
        self._work_completed = _EwmaRate(ewma_alpha)
        self._latency_ewma = 0.0
        self._rss_checked_at = 0.0
        self._rss_mb = 0.0

    # --- lifecycle ----------------------------------------------------------------

    def estimate_work(self, payload_size: int) -> float:
        return self._base_cost + self._per_char * payload_size

    def try_acquire(self, payload_size: int) -> AdmissionTicket:
        work = self.estimate_work(payload_size)
        with self._lock:
            self._arrivals.observe(work)
            state = self._load_state_locked()

            if state == LoadState.CRITICAL:
                raise AdmissionRejectedError(self._retry_after_locked(work), state.value, "critical_pressure")
            if self._in_flight >= self._max_in_flight:
                raise AdmissionRejectedError(
                    self._retry_after_locked(work),
                    LoadState.OVERLOADED.value,
                    "in_flight_limit",
                )
            if self._outstanding_work + work > self._max_backlog:
                raise AdmissionRejectedError(
                    self._retry_after_locked(work),
                    LoadState.OVERLOADED.value,
                    "backlog_limit",
                )

            self._in_flight += 1
            self._outstanding_work += work
            return AdmissionTicket(work_units=work, started=time.monotonic())

    def release(self, ticket: AdmissionTicket) -> None:
        latency = time.monotonic() - ticket.started
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._outstanding_work = max(0.0, self._outstanding_work - ticket.work_units)
            self._completions.observe(1.0)
            self._work_completed.observe(ticket.work_units)
            self._latency_ewma = 0.2 * latency + 0.8 * self._latency_ewma

    # --- state --------------------------------------------------------------------

    def _rss_now_mb(self) -> float:
        now = time.monotonic()
        if now - self._rss_checked_at > 1.0:  # /proc read at most once per second
            self._rss_mb = _process_rss_mb()
            self._rss_checked_at = now
        return self._rss_mb

    def _load_state_locked(self) -> LoadState:
        if self._rss_now_mb() > self._rss_limit_mb:
            return LoadState.CRITICAL
        if self._store is not None and getattr(self._store, "authoritative_degraded", False):
            return LoadState.STORAGE_DEGRADED
        if self._in_flight >= self._max_in_flight or self._outstanding_work > 0.8 * self._max_backlog:
            return LoadState.OVERLOADED
        if self._store is not None and getattr(self._store, "cache_degraded", False):
            return LoadState.CACHE_DEGRADED
        return LoadState.NORMAL

    def load_state(self) -> LoadState:
        with self._lock:
            return self._load_state_locked()

    def _retry_after_locked(self, incoming_work: float) -> float:
        service_work_rate = self._work_completed.rate
        if service_work_rate <= 0:
            # no completions observed yet: fall back to a latency-based guess
            base_wait = max(self._latency_ewma, self._min_retry)
        else:
            base_wait = (self._outstanding_work + incoming_work) / service_work_rate

        arrival_rate = self._arrivals.rate
        pressure = max(1.0, arrival_rate / service_work_rate) if service_work_rate > 0 else 1.0

        modifier = 1.0
        if self._store is not None and getattr(self._store, "authoritative_degraded", False):
            modifier = 2.0  # storage needs breathing room beyond pure throughput math

        # Pressure-scaled floor: with a bare min_retry floor, a checker that
        # retries right after Retry-After keeps the storm at full strength
        # (measured: goodput fell 348->233 req/s when offered load doubled).
        # Deep overload must push retries further out, not just reject faster.
        effective_min = min(self._min_retry * max(1.0, pressure / 2.0), self._max_retry / 3.0)

        retry = base_wait * pressure * self._safety * modifier
        return round(min(max(retry, effective_min), self._max_retry), 1)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._load_state_locked().value,
                "in_flight": self._in_flight,
                "outstanding_work": round(self._outstanding_work),
                "arrival_work_rate": round(self._arrivals.rate, 1),
                "service_work_rate": round(self._work_completed.rate, 1),
                "completion_rate": round(self._completions.rate, 2),
                "latency_ewma_ms": round(self._latency_ewma * 1000, 1),
                "rss_mb": round(self._rss_mb, 1),
            }
