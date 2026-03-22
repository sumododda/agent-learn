"""Server-side encryption for user API keys.

Uses HMAC-SHA256 for key derivation (pepper + per-user salt) and AES-256-GCM
for authenticated encryption. Decryptable by the server anytime without
user password — credentials are available immediately after any restart.
"""
import base64
import hmac
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SALT_LEN = 16  # 128-bit salt
_NONCE_LEN = 12  # 96-bit GCM nonce


def generate_salt() -> bytes:
    """Generate a cryptographically random salt."""
    return os.urandom(_SALT_LEN)


def derive_key(salt: bytes, pepper: bytes) -> bytearray:
    """Derive a 256-bit encryption key from server pepper + per-user salt.

    HMAC-SHA256(pepper, salt) gives a unique 256-bit key per user.
    No password needed — decryptable by the server anytime.
    """
    raw = hmac.digest(pepper, salt, "sha256")
    return bytearray(raw)


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
