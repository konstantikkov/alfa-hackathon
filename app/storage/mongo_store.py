from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from app.config.logging import get_logger
from app.core.interfaces import MappingStore

# Module-level constants for repeated literals (S1192).
_EXPIRES_AT = "expires_at"
_RECORD = "record"

logger = get_logger("app.storage.mongo")


def _utcnow() -> datetime:
    # pymongo returns naive UTC datetimes by default; keep comparisons consistent.
    return datetime.now(UTC).replace(tzinfo=None)


class MongoMappingRepository(MappingStore):
    """Authoritative durable mapping store.

    Document shape: {"_id": payload_id, "expires_at": <BSON date>, "record": {...}}.
    The record itself is stored nested and opaque -- no PII field is ever indexed
    (the only indexes are _id and the TTL index on expires_at). Atomicity for
    concurrent first requests comes from the unique _id insert: exactly one
    insert_one wins, the loser gets DuplicateKeyError.
    """

    def __init__(self, collection: Collection, *, batch_grace: bool = False) -> None:
        self._collection = collection
        # Micro-batching of DEMASKED_GRACE transitions: at capacity every demask
        # produced its own update_one round trip; a 100ms bulk_write flush cuts
        # that to ~10 Mongo ops/sec regardless of RPS. The demask response never
        # waited for this write anyway (mark_demasked is fire-and-tolerate), so
        # the only semantic change is <=100ms extra until the state is durable.
        self._batch_grace = batch_grace
        # TTL-index creation is retried lazily: if Mongo was down at startup,
        # a fire-and-forget ensure_indexes() would otherwise leave mappings
        # unpurgeable until the next restart.
        self._indexes_ready = False
        self._last_index_attempt = 0.0
        self._grace_lock = threading.Lock()
        self._grace_pending: dict[str, int] = {}
        self._grace_thread: threading.Thread | None = None
        if batch_grace:
            self._grace_thread = threading.Thread(
                target=self._grace_flush_loop, daemon=True, name="mongo-grace-batcher"
            )
            self._grace_thread.start()

    @classmethod
    def from_url(
        cls,
        url: str,
        db_name: str = "pii",
        collection_name: str = "mappings",
        *,
        server_selection_timeout_ms: int = 500,
        socket_timeout_ms: int = 1500,
        batch_grace: bool = True,
    ) -> MongoMappingRepository:
        # Fail fast + lazy connect: a down Mongo must surface as a quick
        # StorageUnavailableError (the breaker handles it), not multi-second
        # stalls; connect=False keeps construction non-blocking at startup.
        client: MongoClient = MongoClient(
            url,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
            socketTimeoutMS=socket_timeout_ms,
            connectTimeoutMS=server_selection_timeout_ms,
            connect=False,
        )
        return cls(client[db_name][collection_name], batch_grace=batch_grace)

    def ensure_indexes(self) -> None:
        # expireAfterSeconds=0 => Mongo's TTL monitor deletes the doc once
        # expires_at passes. This is the emergency janitor; the primary cleanup
        # is the explicit DEMASKED_GRACE -> purge lifecycle.
        self._collection.create_index(_EXPIRES_AT, expireAfterSeconds=0)
        self._indexes_ready = True

    def _ensure_indexes_lazy(self) -> None:
        if self._indexes_ready:
            return
        now = time.monotonic()
        if now - self._last_index_attempt < 60.0:
            return  # throttle: one retry a minute, piggybacked on real traffic
        self._last_index_attempt = now
        try:
            self.ensure_indexes()
        except Exception as exc:
            logger.debug("ttl_index_retry_deferred", error=repr(exc))

    def ping(self) -> bool:
        self._collection.database.client.admin.command("ping")
        return True

    def _is_live(self, doc: dict) -> bool:
        expires_at = doc.get(_EXPIRES_AT)
        return expires_at is None or expires_at > _utcnow()

    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        self._ensure_indexes_lazy()
        doc = {
            "_id": payload_id,
            _EXPIRES_AT: _utcnow() + timedelta(seconds=ttl_seconds),
            _RECORD: record,
        }
        try:
            self._collection.insert_one(doc)
            return True
        except DuplicateKeyError:
            # The TTL monitor runs every ~60s, so an expired doc can still occupy
            # the _id. Claim it atomically: replace only if it is actually expired.
            replaced = self._collection.replace_one(
                {"_id": payload_id, _EXPIRES_AT: {"$lte": _utcnow()}}, doc
            )
            return replaced.modified_count > 0

    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        self._collection.replace_one(
            {"_id": payload_id},
            {
                "_id": payload_id,
                _EXPIRES_AT: _utcnow() + timedelta(seconds=ttl_seconds),
                _RECORD: record,
            },
            upsert=True,
        )

    def get(self, payload_id: str) -> dict | None:
        doc = self._collection.find_one({"_id": payload_id})
        if doc is None or not self._is_live(doc):
            return None
        return doc[_RECORD]

    def delete(self, payload_id: str) -> None:
        self._collection.delete_one({"_id": payload_id})

    def mark_demasked(self, payload_id: str, grace_seconds: int) -> None:
        # Targeted $set instead of the ABC's read-modify-replace: under load the
        # full-document round trip per demask kept Mongo CPU-bound (measured
        # ~90% of a core during the 500 req/s highload level).
        if self._batch_grace:
            with self._grace_lock:
                self._grace_pending[payload_id] = grace_seconds
                should_flush = len(self._grace_pending) >= 500
            if should_flush:
                self.flush_grace_now()
            return
        self._collection.update_one({"_id": payload_id}, self._grace_update(grace_seconds))

    @staticmethod
    def _grace_update(grace_seconds: int) -> dict:
        return {
            "$set": {
                "record.state": "DEMASKED_GRACE",
                "record.demasked_at": time.time(),
                _EXPIRES_AT: _utcnow() + timedelta(seconds=grace_seconds),
            }
        }

    def flush_grace_now(self) -> int:
        with self._grace_lock:
            pending, self._grace_pending = self._grace_pending, {}
        if not pending:
            return 0
        ops = [UpdateOne({"_id": pid}, self._grace_update(g)) for pid, g in pending.items()]
        self._collection.bulk_write(ops, ordered=False)
        return len(ops)

    def _grace_flush_loop(self) -> None:
        while True:
            time.sleep(0.1)
            try:
                self.flush_grace_now()
            except Exception as exc:
                logger.debug("grace_flush_failed", error=repr(exc))

    def health(self) -> dict:
        try:
            ok = self.ping()
        except Exception:
            ok = False
        return {"backend": "mongo", "ok": ok, "state": "closed"}
