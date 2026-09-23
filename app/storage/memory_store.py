from __future__ import annotations

import threading
import time
from typing import Any

from app.core.interfaces import MappingStore


class InMemoryMappingStore(MappingStore):
    """Process-local fallback used in tests and single-process dev runs.

    Not shared across worker processes -- production deployments must use
    RedisMappingStore so all workers see the same mapping.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at <= time.time()

    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        with self._lock:
            existing = self._data.get(payload_id)
            if existing is not None and not self._is_expired(existing[0]):
                return False
            self._data[payload_id] = (time.time() + ttl_seconds, record)
            return True

    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        with self._lock:
            self._data[payload_id] = (time.time() + ttl_seconds, record)

    def get(self, payload_id: str) -> dict | None:
        with self._lock:
            entry = self._data.get(payload_id)
            if entry is None:
                return None
            expires_at, record = entry
            if self._is_expired(expires_at):
                del self._data[payload_id]
                return None
            return record

    def delete(self, payload_id: str) -> None:
        with self._lock:
            self._data.pop(payload_id, None)
