"""X-Hub-Signature-256 verification. Every webhook POST must pass this."""

import hashlib
import hmac


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    prefix, _, received = header.partition("=")
    if prefix.strip().lower() != "sha256" or not received:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received.strip())


def sign(secret: str, body: bytes) -> str:
    """The header Meta would send. Used by tests and the simulator."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
