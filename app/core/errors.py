from __future__ import annotations


class PiiServiceError(Exception):
    """Base for errors the API layer knows how to map to a status code."""


class ConsumerNotFoundError(PiiServiceError):
    pass


class ConsumerDisabledError(PiiServiceError):
    pass


class PayloadConflictError(PiiServiceError):
    """Concurrent first requests reused one payload ID for different content."""


class StorageUnavailableError(PiiServiceError):
    """The authoritative mapping store cannot serve this request safely.

    Raised when a durable write is required but Mongo is down, or when a read
    cannot distinguish "no record" from "record exists but store unreachable".
    The API layer maps this to 503 -- fail closed, never guess about mappings.
    """


class AdmissionRejectedError(PiiServiceError):
    """Backpressure: the service is over capacity; retry after `retry_after` seconds."""

    def __init__(self, retry_after: float, load_state: str, reason: str) -> None:
        super().__init__(reason)
        self.retry_after = retry_after
        self.load_state = load_state
        self.reason = reason
