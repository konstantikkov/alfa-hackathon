from __future__ import annotations

import hashlib
import hmac

_UTF8 = "utf-8"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode(_UTF8)).hexdigest()


def payload_fingerprint(value: str, secret: str | None = None) -> str:
    """Fingerprint used to match retried/demask payloads without storing them.

    HMAC-SHA-256 when a server secret is configured (an attacker with read access
    to the store cannot confirm a guessed payload by hashing it); plain SHA-256
    is the accepted baseline when no secret management is available.
    """
    if secret:
        return hmac.new(secret.encode(_UTF8), value.encode(_UTF8), hashlib.sha256).hexdigest()
    return sha256_hex(value)
