"""Shared test doubles and fixture builders.

Fixture values are deliberately all-zero or derived at runtime from the
checksum helpers -- no test contains anything resembling a real card number,
INN, passport, phone number, or email address.
"""

from __future__ import annotations

from app.core.interfaces import MappingStore
from app.detectors.checksums import inn_valid, luhn_valid
from app.storage.memory_store import InMemoryMappingStore

DUMMY_PASSPORT = "0000 000000"
DUMMY_PHONE = "+7 900 000-00-00"
DUMMY_EMAIL = "test.user@example.com"


def dummy_card(length: int = 16) -> str:
    """All-zero card body completed to a Luhn-valid number at runtime."""
    for digit in "0123456789":
        candidate = "0" * (length - 1) + digit
        if luhn_valid(candidate):
            return candidate
    raise AssertionError("no completing Luhn digit exists")


def dummy_inn(length: int = 10) -> str:
    """All-zero INN; the zero check digits are checksum-correct by construction."""
    value = "0" * length
    assert inn_valid(value)
    return value


class FlakyStore(MappingStore):
    """In-memory store with switchable failure injection per operation."""

    def __init__(self, inner: MappingStore | None = None) -> None:
        self.inner = inner or InMemoryMappingStore()
        self.down = False
        self.calls: list[str] = []

    def _guard(self, op: str) -> None:
        self.calls.append(op)
        if self.down:
            raise ConnectionError(f"injected failure in {op}")

    def save_if_absent(self, payload_id: str, record: dict, ttl_seconds: int) -> bool:
        self._guard("save_if_absent")
        return self.inner.save_if_absent(payload_id, record, ttl_seconds)

    def update(self, payload_id: str, record: dict, ttl_seconds: int) -> None:
        self._guard("update")
        self.inner.update(payload_id, record, ttl_seconds)

    def get(self, payload_id: str) -> dict | None:
        self._guard("get")
        return self.inner.get(payload_id)

    def delete(self, payload_id: str) -> None:
        self._guard("delete")
        self.inner.delete(payload_id)
