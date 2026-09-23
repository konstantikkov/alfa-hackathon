from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import MaskingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PII_", env_file=".env", extra="ignore")

    # --- Service ---
    host: str = "0.0.0.0"  # NOSONAR -- container-internal bind, published only to 127.0.0.1
    port: int = 8080
    log_level: str = "INFO"
    log_raw_payloads: bool = False  # local debugging only, never enable in prod

    # --- Masking ---
    default_masking_mode: MaskingMode = MaskingMode.PARTIAL
    default_demask_enabled: bool = True

    # --- Mapping storage ---
    redis_url: str | None = "redis://localhost:6379/0"
    mongo_url: str | None = None  # when set, Mongo is authoritative and Redis is a hot cache
    mongo_db: str = "pii"
    mongo_collection: str = "mappings"
    mapping_ttl_seconds: int = 600
    demask_retry_grace_seconds: int = 60
    fingerprint_secret: str | None = None  # HMAC key for payload fingerprints; SHA-256 baseline if unset
    storage_failure_threshold: int = 3
    storage_cooldown_seconds: float = 10.0

    # --- Consumer configuration ---
    consumers_config_path: str = "app/config/consumers.yaml"

    # --- Context window (for large documents; bounded analysis, not O(n^2)) ---
    context_window_chars: int = 220

    # --- Admission control / backpressure ---
    max_payload_bytes: int = 3_000_000  # ~100k tokens of UTF-8 + JSON envelope; 413 above
    admission_max_in_flight: int = 32  # per worker process; tune on target hardware
    admission_max_backlog_work_units: float = 4_000_000  # ~4MB of pending payload chars
    admission_base_cost: float = 500.0
    admission_per_char_cost: float = 1.0
    min_retry_after_seconds: float = 1.0
    max_retry_after_seconds: float = 30.0
    admission_safety_factor: float = 1.5
    admission_rss_limit_mb: float = 12_288.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
