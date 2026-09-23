"""Pure-ASGI admission gate.

Runs on the event loop BEFORE the sync-endpoint threadpool hop. Under
saturation the threadpool (and everything queued behind it) is exactly where
requests go to die waiting; a gate inside the endpoint cannot reject what is
already stuck in front of it. Measured on the demo host: with the in-endpoint
gate, 500 req/s produced 10-second p95 and mass timeouts with almost no 429s;
the middleware turns the same overload into instant 429 + Retry-After, which
is what the load checker expects of a healthy overloaded service.
"""

from __future__ import annotations

import json
import time

from app.core.errors import AdmissionRejectedError
from app.metrics.registry import (
    ADMISSION_REJECTED,
    BACKLOG_WORK,
    IN_FLIGHT,
    LOAD_STATE,
    REQUEST_COUNT,
    RETRY_AFTER,
)

_HEADERS = "headers"
_STATES = ("NORMAL", "CACHE_DEGRADED", "STORAGE_DEGRADED", "OVERLOADED", "CRITICAL")
_STATE_GAUGES = {s: LOAD_STATE.labels(state=s) for s in _STATES}


def _set_load_state_metric(state: str) -> None:
    for s, gauge in _STATE_GAUGES.items():
        gauge.set(1.0 if s == state else 0.0)


# Gauges are observability, not control flow: refreshing them (snapshot under a
# lock + 7 mmap writes in multiprocess mode) on EVERY release was itself a
# per-request overhead of the same family as the access log. A 250ms refresh
# is indistinguishable on a 5s-scrape dashboard.
_GAUGE_REFRESH_SECONDS = 0.25


class AdmissionMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        self._last_gauge_refresh = 0.0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/process":
            return await self.app(scope, receive, send)
        admission = getattr(scope["app"].state, "admission", None)
        if admission is None:
            return await self.app(scope, receive, send)

        headers = dict(scope.get(_HEADERS) or [])
        try:
            size = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            size = 0

        settings = getattr(scope["app"].state, "settings", None)
        max_bytes = getattr(settings, "max_payload_bytes", 0) if settings else 0
        if max_bytes and size > max_bytes:
            # Refuse absurd payloads before admission/parsing: the spec caps
            # inputs at ~100k tokens (~1MB of UTF-8 + JSON envelope).
            body = json.dumps({"detail": "payload too large"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    _HEADERS: [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        try:
            ticket = admission.try_acquire(size)
        except AdmissionRejectedError as exc:
            ADMISSION_REJECTED.labels(reason=exc.reason).inc()
            RETRY_AFTER.observe(exc.retry_after)
            _set_load_state_metric(exc.load_state)
            REQUEST_COUNT.labels(direction="rejected", outcome="429").inc()
            body = json.dumps({"detail": "over capacity, retry later"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    _HEADERS: [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(int(exc.retry_after + 0.999)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        try:
            await self.app(scope, receive, send)
        finally:
            admission.release(ticket)
            now = time.monotonic()
            if now - self._last_gauge_refresh >= _GAUGE_REFRESH_SECONDS:
                self._last_gauge_refresh = now
                snapshot = admission.snapshot()
                IN_FLIGHT.set(snapshot["in_flight"])
                BACKLOG_WORK.set(snapshot["outstanding_work"])
                _set_load_state_metric(snapshot["state"])
