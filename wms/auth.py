"""Password hashing — stdlib only, no new dependencies.

Kept deliberately small: PBKDF2-HMAC-SHA256 with a per-user random salt, stored
as a single self-describing string ("algo$iterations$salt$digest"). This keeps
the project runnable with zero setup (see CLAUDE.md) while still storing real
salted password hashes rather than plaintext.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Return a self-describing salted hash for ``password``."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of ``password`` against a stored hash."""
    if not stored:
        return False
    try:
        algo, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != _ALGO:
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
    return hmac.compare_digest(dk.hex(), digest)
