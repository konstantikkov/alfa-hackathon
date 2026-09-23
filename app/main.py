from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.inspect import router as inspect_router
from app.api.routes import router
from app.config.consumers import ConsumerRegistry
from app.config.logging import configure_logging, get_logger
from app.config.settings import get_settings
from app.context.engine import ContextualPrivacyEngine
from app.core.pipeline import PiiPipeline
from app.core.process_service import ProcessService
from app.demasking.demasker import ModeDemasker
from app.detectors.builtin import register_builtin_detectors
from app.detectors.registry import DetectorRegistry
from app.policies.engine import PolicyEngine
from app.replacement.morphology import get_analyzer
from app.storage.memory_store import InMemoryMappingStore
from app.storage.mongo_store import MongoMappingRepository
from app.storage.redis_store import RedisMappingStore
from app.storage.resilient_store import ResilientMappingStore

logger = get_logger("app.main")


def _configured_mongo(settings):
    if not settings.mongo_url:
        return None
    mongo = MongoMappingRepository.from_url(settings.mongo_url, settings.mongo_db, settings.mongo_collection)
    try:
        mongo.ensure_indexes()
    except Exception as exc:
        logger.warning("mongo_index_setup_deferred", error=repr(exc))
    return mongo


def _configured_redis(settings):
    """A constructed-but-unreachable Redis is still returned: it keeps the
    authority and its circuit breaker fails closed per request. Only a client
    that cannot even be constructed (bad URL) yields None."""
    if not settings.redis_url:
        return None
    try:
        store = RedisMappingStore.from_url(settings.redis_url)
    except Exception as exc:
        logger.warning("redis_unreachable_at_startup", error=repr(exc))
        return None
    try:
        store.ping()
    except Exception as exc:
        logger.warning("redis_unreachable_at_startup", error=repr(exc))
    return store


def _build_mapping_store(settings) -> ResilientMappingStore:
    """Mongo authoritative + Redis hot cache when both are configured.

    Fallback ladder for dev/demo environments:
      mongo+redis -> the target architecture;
      redis only  -> Redis is authoritative (legacy single-store mode);
      neither     -> in-memory (single process only).
    The authoritative store is NOT swapped out when unreachable at startup --
    that is a runtime failure the ResilientMappingStore's breaker must surface
    (fail closed), not something to silently paper over with a memory store.
    """
    authoritative = _configured_mongo(settings)
    auth_name = "mongo"
    redis_store = _configured_redis(settings)

    if authoritative is not None:
        # Mongo is authoritative; Redis (even if briefly down) is only a cache
        # and its circuit breaker will probe for recovery.
        cache = redis_store
    elif redis_store is not None:
        authoritative, auth_name, cache = redis_store, "redis", None
    else:
        # Memory is only for explicitly unconfigured development environments.
        # A configured Redis outage must retain the authority and fail closed.
        if settings.redis_url:
            raise ValueError("invalid Redis configuration")
        logger.warning("mapping_store_memory_fallback", reason="no_reachable_mongo_or_redis")
        authoritative, auth_name, cache = InMemoryMappingStore(), "memory", None

    return ResilientMappingStore(
        authoritative,
        cache,
        authoritative_name=auth_name,
        cache_name="redis",
        failure_threshold=settings.storage_failure_threshold,
        cooldown_seconds=settings.storage_cooldown_seconds,
        backfill_ttl_seconds=settings.mapping_ttl_seconds,
    )


def _build_decider(settings):
    return ContextualPrivacyEngine(settings.context_window_chars)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # pymorphy3's dictionary load (~150-300ms) is lazy-cached on first use; done
        # here instead of on a real request so the FIRST user of each worker process
        # never pays it. Detection itself stays lazy -- this only warms the analyzer.
        get_analyzer()

        mapping_store = _build_mapping_store(settings)
        consumer_registry = ConsumerRegistry.load(settings.consumers_config_path)

        detectors = DetectorRegistry()
        register_builtin_detectors(detectors)

        decider = _build_decider(settings)
        policy_engine = PolicyEngine()
        pipeline = PiiPipeline(detectors, decider, policy_engine)
        demasker = ModeDemasker()

        process_service = ProcessService(
            pipeline=pipeline,
            demasker=demasker,
            store=mapping_store,
            consumer_registry=consumer_registry,
            mapping_ttl_seconds=settings.mapping_ttl_seconds,
            demask_retry_grace_seconds=settings.demask_retry_grace_seconds,
            fingerprint_secret=settings.fingerprint_secret,
        )

        from app.admission.controller import AdmissionController

        app.state.admission = AdmissionController(
            max_in_flight=settings.admission_max_in_flight,
            max_backlog_work_units=settings.admission_max_backlog_work_units,
            base_cost=settings.admission_base_cost,
            per_char_cost=settings.admission_per_char_cost,
            min_retry_after=settings.min_retry_after_seconds,
            max_retry_after=settings.max_retry_after_seconds,
            safety_factor=settings.admission_safety_factor,
            rss_limit_mb=settings.admission_rss_limit_mb,
            store=mapping_store,
        )

        app.state.settings = settings
        app.state.mapping_store = mapping_store
        app.state.process_service = process_service
        # Exposed for /inspect (UI debug endpoint) -- ProcessService only exposes
        # the hackathon-contract-shaped process() call, not the pipeline internals.
        app.state.pipeline = pipeline
        app.state.demasker = demasker
        app.state.consumer_registry = consumer_registry

        yield

    from fastapi.responses import ORJSONResponse

    app = FastAPI(
        title="PII Security Proxy",
        version="0.1.0",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
    )
    app.include_router(router)
    app.include_router(inspect_router)

    # Pure-ASGI admission gate: must sit on the event loop, in front of the
    # sync-endpoint threadpool (see app/admission/middleware.py).
    from app.admission.middleware import AdmissionMiddleware

    app.add_middleware(AdmissionMiddleware)

    # Registered LAST: a catch-all mount at "/" would otherwise shadow the routes
    # above if Starlette tried it first (Mount("/") matches every path prefix).
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")
    return app


app = create_app()
