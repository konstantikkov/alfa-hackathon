from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass

from app.config.consumers import ConsumerRegistry
from app.core.enums import MappingState, MaskingMode, ProcessDirection
from app.core.errors import (
    ConsumerDisabledError,
    ConsumerNotFoundError,
    PayloadConflictError,
    StorageUnavailableError,
)
from app.core.hashing import payload_fingerprint
from app.core.interfaces import Demasker, MappingStore
from app.core.models import TransformResult
from app.core.pipeline import PiiPipeline
from app.metrics.registry import (
    ENTITIES_DETECTED,
    ENTITIES_MASKED,
    REDIS_LATENCY,
    STORAGE_DEGRADED_EVENTS,
)
from app.storage.serialization import mapping_from_dict, mapping_to_dict

RECORD_VERSION = 2

# Record field names used in more than two places (S1192).
_ORIGINAL_FP = "original_fingerprint"
_OCCURRENCES = "occurrences"

# labels() does a locked dict lookup; bind the two hot series once.
_GET_LATENCY = REDIS_LATENCY.labels(op="get")
_SAVE_LATENCY = REDIS_LATENCY.labels(op="save_if_absent")


def _timed_get(store: MappingStore, payload_id: str) -> dict | None:
    start = time.perf_counter()
    try:
        return store.get(payload_id)
    finally:
        _GET_LATENCY.observe(time.perf_counter() - start)


def _timed_save_if_absent(store: MappingStore, payload_id: str, record: dict, ttl: int) -> bool:
    start = time.perf_counter()
    try:
        return store.save_if_absent(payload_id, record, ttl)
    finally:
        _SAVE_LATENCY.observe(time.perf_counter() - start)


@dataclass
class ProcessOutcome:
    result: str
    direction: ProcessDirection
    entities_detected: int = 0
    entities_masked: int = 0


def build_record(transform: TransformResult, payload: str, demask_enabled: bool, secret: str | None) -> dict:
    """Mapping record: fingerprints + per-entity mappings + per-occurrence spans.

    Deliberately does NOT contain the original or transformed document: for a
    100k-token payload the record stays proportional to the number of entities,
    and a store compromise leaks mappings but not the surrounding documents.
    """
    return {
        "version": RECORD_VERSION,
        "state": MappingState.ACTIVE.value,
        "mode": transform.mode.value,
        _ORIGINAL_FP: payload_fingerprint(payload, secret),
        "transformed_fingerprint": payload_fingerprint(transform.text, secret),
        "no_pii": not transform.mappings,
        "demask_enabled": demask_enabled,
        "mappings": [mapping_to_dict(m) for m in transform.mappings],
        _OCCURRENCES: [asdict(o) for o in transform.occurrences],
        "created_at": time.time(),
        "demasked_at": None,
    }


def _splice(payload: str, occurrences: list[dict], start_key: str, end_key: str, insert_key: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for occ in sorted(occurrences, key=lambda o: o[start_key]):
        pieces.append(payload[cursor : occ[start_key]])
        pieces.append(occ[insert_key])
        cursor = occ[end_key]
    pieces.append(payload[cursor:])
    return "".join(pieces)


def apply_mask_from_record(payload: str, record: dict) -> str:
    """payload verified (by fingerprint) to be the original text -> re-splice masks."""
    return _splice(payload, record[_OCCURRENCES], "original_start", "original_end", "masked")


def apply_demask_from_record(payload: str, record: dict) -> str:
    """payload verified (by fingerprint) to be the exact transformed text -> re-splice originals."""
    return _splice(payload, record[_OCCURRENCES], "masked_start", "masked_end", "original")


class ProcessService:
    """Implements the hackathon /process contract: one endpoint, direction inferred
    from payload_id state via payload fingerprints, idempotent under retries,
    race-safe under concurrent first requests (authoritative-store atomic insert).
    """

    def __init__(
        self,
        pipeline: PiiPipeline,
        demasker: Demasker,
        store: MappingStore,
        consumer_registry: ConsumerRegistry,
        mapping_ttl_seconds: int,
        demask_retry_grace_seconds: int = 60,
        fingerprint_secret: str | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._demasker = demasker
        self._store = store
        self._consumers = consumer_registry
        self._ttl = mapping_ttl_seconds
        self._grace = demask_retry_grace_seconds
        self._secret = fingerprint_secret

    def process(self, payload: str, payload_id: str, consumer_id: str | None = None) -> ProcessOutcome:
        consumer = self._consumers.resolve(consumer_id)
        if consumer is None:
            raise ConsumerNotFoundError(consumer_id or "<default>")
        if not consumer.enabled:
            raise ConsumerDisabledError(consumer.consumer_id)

        try:
            record = _timed_get(self._store, payload_id)
        except StorageUnavailableError:
            return self._answer_without_storage(payload, consumer)
        if record is None:
            return self._first_request(payload, payload_id, consumer)

        fp = payload_fingerprint(payload, self._secret)
        if fp == record[_ORIGINAL_FP]:
            # Retry of the original request: same masked result, no state change.
            return ProcessOutcome(
                result=apply_mask_from_record(payload, record),
                direction=ProcessDirection.RETRY_MASK,
            )

        if not record.get("demask_enabled", True) or not consumer.demask:
            return ProcessOutcome(result=payload, direction=ProcessDirection.DEMASK)

        if fp == record["transformed_fingerprint"]:
            result = apply_demask_from_record(payload, record)
            self._transition_to_grace(payload_id, record)
            return ProcessOutcome(result=result, direction=ProcessDirection.DEMASK)

        # LLM-modified transformed text: content-based demasking via mappings.
        mappings = [mapping_from_dict(m) for m in record["mappings"]]
        mode = MaskingMode(record["mode"])
        result = self._demasker.demask(payload, mappings, mode)
        self._transition_to_grace(payload_id, record)
        return ProcessOutcome(result=result, direction=ProcessDirection.DEMASK)

    def _first_request(self, payload: str, payload_id: str, consumer) -> ProcessOutcome:
        transform = self._pipeline.process(
            text=payload,
            mode=consumer.masking_mode,
            consumer=consumer,
            session_seed=payload_id,
        )
        record = build_record(transform, payload, consumer.demask, self._secret)
        try:
            created = _timed_save_if_absent(self._store, payload_id, record, self._ttl)
        except StorageUnavailableError:
            if record["no_pii"]:
                # Nothing reversible was created; answering is safe even without
                # durable state (idempotency of a no-op transform is trivial).
                STORAGE_DEGRADED_EVENTS.labels(kind="no_pii_unpersisted").inc()
                return ProcessOutcome(result=transform.text, direction=ProcessDirection.MASK)
            raise  # fail closed: never hand out a masked text we cannot demask later

        if not created:
            # The winner may have processed different content or policy. Never
            # return our unpersisted mapping: verify and use the stored result.
            STORAGE_DEGRADED_EVENTS.labels(kind="save_race_lost").inc()
            winner = _timed_get(self._store, payload_id)
            if winner is None:
                raise StorageUnavailableError("winning mapping unavailable")
            if winner[_ORIGINAL_FP] != record[_ORIGINAL_FP]:
                raise PayloadConflictError("payload_id already used for different content")
            return ProcessOutcome(
                result=apply_mask_from_record(payload, winner),
                direction=ProcessDirection.RETRY_MASK,
            )

        detected = len(transform.detected_entities) + len(transform.kept_entities)
        ENTITIES_DETECTED.inc(detected)
        ENTITIES_MASKED.inc(len(transform.detected_entities))
        return ProcessOutcome(
            result=transform.text,
            direction=ProcessDirection.MASK,
            entities_detected=detected,
            entities_masked=len(transform.detected_entities),
        )

    _TOKEN_PATTERN = re.compile(r"<[A-Z_]+_\d+>")

    def _answer_without_storage(self, payload: str, consumer) -> ProcessOutcome:
        """Storage is fully unreachable and we cannot even tell whether this
        payload_id has a mapping. The only requests we can still answer safely
        are ones that need no reversible state at all: no PII detected and no
        trace of previously issued mask tokens. Everything else fails closed.

        (A previously SYNTHETIC-masked text also fails closed here: its synthetic
        names/documents are themselves detected as PII, so `mappings` is non-empty.)
        """
        if self._TOKEN_PATTERN.search(payload) or "**" in payload:
            # Mask-token or partial-mask (star-run) traces: this may well be a
            # demask retry for a mapping we cannot reach. Fail closed, not guess.
            raise StorageUnavailableError("possible demask retry while storage is down")
        transform = self._pipeline.process(
            text=payload,
            mode=consumer.masking_mode,
            consumer=consumer,
            session_seed="degraded",
        )
        if transform.mappings:
            raise StorageUnavailableError("reversible mapping required while storage is down")
        STORAGE_DEGRADED_EVENTS.labels(kind="no_pii_unpersisted").inc()
        return ProcessOutcome(result=payload, direction=ProcessDirection.MASK)

    def _transition_to_grace(self, payload_id: str, record: dict) -> None:
        """ACTIVE -> DEMASKED_GRACE. Never fails the response: the demask already
        succeeded; a lost transition only means the mapping lives to its full TTL."""
        if record.get("state") == MappingState.DEMASKED_GRACE.value:
            return  # already in grace; do not extend it on every retry
        try:
            self._store.mark_demasked(payload_id, self._grace)
        except Exception:
            STORAGE_DEGRADED_EVENTS.labels(kind="grace_transition_failed").inc()
