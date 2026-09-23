from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.enums import MaskingMode, PIIType

DEFAULT_CONSUMER_ID = "default"


@dataclass(frozen=True)
class ConsumerConfig:
    consumer_id: str
    enabled: bool = True
    mask_types: frozenset[PIIType] | None = None  # None = all supported types
    demask: bool = True
    masking_mode: MaskingMode = MaskingMode.PARTIAL

    def should_mask_type(self, pii_type: PIIType) -> bool:
        if self.mask_types is None:
            return True
        return pii_type in self.mask_types


class ConsumerRegistry:
    """Per-consumer policy: which systems may call the service and how each is configured.

    The hackathon `/process` contract never sends a consumer_id, so callers without one
    fall back to `default_consumer`. Internal/product API paths pass `consumer_id` and get
    per-system enable flags, type allow-lists, demask toggles and masking mode.
    """

    def __init__(self, default_consumer: ConsumerConfig, consumers: dict[str, ConsumerConfig]):
        self._default = default_consumer
        self._consumers = consumers

    def resolve(self, consumer_id: str | None) -> ConsumerConfig | None:
        if consumer_id is None:
            return self._default
        return self._consumers.get(consumer_id)

    @classmethod
    def load(cls, path: str | Path) -> ConsumerRegistry:
        p = Path(path)
        if not p.exists():
            return cls(default_consumer=_default_consumer_config(), consumers={})

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        default_raw = raw.get("default", {})
        default = _parse_consumer(DEFAULT_CONSUMER_ID, default_raw)

        consumers = {}
        for consumer_id, cfg in (raw.get("consumers") or {}).items():
            consumers[consumer_id] = _parse_consumer(consumer_id, cfg)

        return cls(default_consumer=default, consumers=consumers)


def _default_consumer_config() -> ConsumerConfig:
    return ConsumerConfig(
        consumer_id=DEFAULT_CONSUMER_ID,
        enabled=True,
        mask_types=None,
        demask=True,
        masking_mode=MaskingMode.PARTIAL,
    )


def _parse_consumer(consumer_id: str, cfg: dict) -> ConsumerConfig:
    mask_types_raw = cfg.get("mask_types")
    mask_types = frozenset(PIIType(t) for t in mask_types_raw) if mask_types_raw is not None else None
    return ConsumerConfig(
        consumer_id=consumer_id,
        enabled=cfg.get("enabled", True),
        mask_types=mask_types,
        demask=cfg.get("demask", True),
        masking_mode=MaskingMode(cfg.get("masking_mode", "partial")),
    )
