"""Password hashing, JWT tokens and field encryption helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_strength_errors(password: str) -> list[str]:
    """Return a list of human-readable policy violations."""
    errors: list[str] = []
    if len(password) < settings.password_min_length:
        errors.append("password_too_short")
    if not any(c.isalpha() for c in password):
        errors.append("password_needs_letter")
    if not any(c.isdigit() for c in password):
        errors.append("password_needs_digit")
    return errors


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    """Return (token, sha256-hash-to-store, expiry)."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, hash_token(token), expires.replace(tzinfo=None)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("unexpected token type")
    return payload


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Sensitive field encryption (national ID, account numbers, ...)
# ---------------------------------------------------------------------------
def _keystream(salt: bytes) -> bytes:
    material = (settings.data_encryption_key + settings.secret_key).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, salt, 100_000, dklen=32)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a short sensitive string.

    Format: ``enc:v1:<base64(salt|ciphertext)>`` using a keystream XOR with an
    HMAC tag. Adequate for prototype PII at rest; document the production
    recommendation to use a KMS / Postgres pgcrypto in docs/SECURITY.md.
    """
    if plaintext is None:
        return ""
    salt = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    stream = _keystream(salt)
    cipher = bytes(b ^ stream[i % len(stream)] for i, b in enumerate(data))
    tag = hmac.new(stream, cipher, hashlib.sha256).digest()[:16]
    blob = base64.urlsafe_b64encode(salt + tag + cipher).decode("ascii")
    return f"enc:v1:{blob}"


def decrypt_value(stored: str) -> Optional[str]:
    if not stored or not stored.startswith("enc:v1:"):
        return stored or None
    try:
        raw = base64.urlsafe_b64decode(stored.split(":", 2)[2].encode("ascii"))
        salt, tag, cipher = raw[:16], raw[16:32], raw[32:]
        stream = _keystream(salt)
        if not hmac.compare_digest(hmac.new(stream, cipher, hashlib.sha256).digest()[:16], tag):
            return None
        plain = bytes(b ^ stream[i % len(stream)] for i, b in enumerate(cipher))
        return plain.decode("utf-8")
    except Exception:  # pragma: no cover - defensive
        return None


def mask_sensitive(value: str | None, keep_last: int = 3) -> str | None:
    """``123456789012`` -> ``*********012`` (safe to display and store)."""
    if not value:
        return None
    if len(value) <= keep_last:
        return "*" * len(value)
    return "*" * (len(value) - keep_last) + value[-keep_last:]
