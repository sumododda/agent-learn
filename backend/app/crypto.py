"""Password-based encryption for user API keys.

Uses Argon2id for key derivation (OWASP recommended) and AES-256-GCM
for authenticated encryption. A server-side HMAC pepper adds defense
against DB-only breaches.
"""
import base64
import hmac
import json
import os
from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# KDF parameters (OWASP 2025 / RFC 9106 recommended for Argon2id)
_TIME_COST = 3
_MEMORY_COST = 65536  # 64 MiB in KiB
_PARALLELISM = 4
_HASH_LEN = 32  # 256-bit key
_SALT_LEN = 16  # 128-bit salt
_NONCE_LEN = 12  # 96-bit GCM nonce

# Test-friendly overrides (set by conftest.py to speed up tests)
_test_time_cost = None
_test_memory_cost = None


def generate_salt() -> bytes:
    """Generate a cryptographically random salt."""
    return os.urandom(_SALT_LEN)


def derive_key(password: str, salt: bytes, pepper: bytes) -> bytearray:
    """Derive a 256-bit encryption key from password + salt + pepper.

    Steps:
    1. HMAC-SHA256(pepper, password) — pre-mix pepper before KDF
    2. Argon2id(peppered_password, salt) — memory-hard KDF

    Returns a bytearray (mutable, can be zeroed after use).
    """
    peppered = hmac.digest(pepper, password.encode("utf-8"), "sha256")
    raw_key = hash_secret_raw(
        secret=peppered,
        salt=salt,
        time_cost=_test_time_cost or _TIME_COST,
        memory_cost=_test_memory_cost or _MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_HASH_LEN,
        type=Type.ID,
    )
    return bytearray(raw_key)


def encrypt_credentials(key: bytearray, plaintext: str) -> str:
    """Encrypt credential JSON with AES-256-GCM.

    Returns base64-encoded string: nonce(12) || ciphertext || tag(16)
    """
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(bytes(key))
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_credentials(key: bytearray, blob: str) -> str:
    """Decrypt a base64-encoded AES-256-GCM blob.

    Raises cryptography.exceptions.InvalidTag on wrong key or tampering.
    """
    raw = base64.b64decode(blob)
    nonce = raw[:_NONCE_LEN]
    ciphertext = raw[_NONCE_LEN:]
    aesgcm = AESGCM(bytes(key))
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def generate_credential_hint(provider: str, credentials: dict) -> str:
    """Generate a provider-aware display hint for stored credentials.

    - For API key providers: ****<last4>
    - For Vertex AI (service account JSON): ****@<project>.iam...
    """
    if provider == "vertex_ai" and "vertex_credentials" in credentials:
        try:
            sa = json.loads(credentials["vertex_credentials"])
            email = sa.get("client_email", "")
            if "@" in email:
                return f"****{email[email.index('@'):]}"
        except (json.JSONDecodeError, TypeError):
            pass
        return "****"

    # Default: use api_key last 4 chars
    api_key = credentials.get("api_key", "")
    if len(api_key) >= 4:
        return f"****{api_key[-4:]}"
    return "****"


def zero_buffer(buf: bytearray) -> None:
    """Best-effort secure zeroing of a bytearray (CPython)."""
    for i in range(len(buf)):
        buf[i] = 0
