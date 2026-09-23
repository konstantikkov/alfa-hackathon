from __future__ import annotations

import orjson
import redis

from app.core.interfaces import MappingStore

_KEY_PREFIX = "pii:"


def _key(payload_id: str) -> str:
    return f"{_KEY_PREFIX}{payload_id}"


class RedisMappingStore(MappingStore):
    """Redis-backed mapping vault. One key per payload_id, TTL-bound, JSON body.

    Never store anything here that shouldn't outlive the process -- this is the only
    place original PII values are persisted, and only inside the protected perimeter.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> RedisMappingStore:
        # Fail fast: without socket timeouts a Redis outage costs seconds PER
        # ATTEMPT before the circuit breaker even sees the failure; the cache
        # tier must degrade in milliseconds, not stall requests.
        return cls(
            redis.Redis.from_url(
                url,
                decode_responses=False,
                socket_connect_timeout=0.3,
                socket_timeout=0.5,
                retry_on_timeout=False,
            )
        )

    def ping(self) -> bool:
        return bool(self._client.ping())

    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        payload = orjson.dumps(record)
        return bool(self._client.set(_key(payload_id), payload, nx=True, ex=ttl_seconds))

    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        payload = orjson.dumps(record)
        self._client.set(_key(payload_id), payload, ex=ttl_seconds)

    def get(self, payload_id: str) -> dict | None:
        raw = self._client.get(_key(payload_id))
        if raw is None:
            return None
        return orjson.loads(raw)

    def delete(self, payload_id: str) -> None:
        self._client.delete(_key(payload_id))
