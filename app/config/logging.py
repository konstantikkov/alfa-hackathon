from __future__ import annotations

import logging
import sys

import orjson
import structlog


def _orjson_dumps(obj, **_kw) -> str:
    # stdlib json spent measurable per-request time rendering the structured
    # log line; orjson is already a dependency and ~5x faster here.
    return orjson.dumps(obj).decode()


# Fields that must never appear in a log record. This is a last-resort safety net --
# the real guarantee is that call sites never pass raw PII into log calls at all.
_FORBIDDEN_KEYS = {
    "payload",
    "original",
    "original_text",
    "value",
    "text",
    "mapping",
    "mappings",
}


def _drop_forbidden_fields(_logger, _method_name, event_dict):
    for key in _FORBIDDEN_KEYS:
        event_dict.pop(key, None)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _drop_forbidden_fields,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(serializer=_orjson_dumps),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
