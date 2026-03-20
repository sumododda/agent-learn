"""Tests for password-based encryption (crypto.py)."""
import json
import pytest
from app.crypto import (
    generate_salt,
    derive_key,
    encrypt_credentials,
    decrypt_credentials,
    generate_credential_hint,
    zero_buffer,
    _test_time_cost,
    _test_memory_cost,
)
from cryptography.exceptions import InvalidTag

# Use fast params for tests
import app.crypto as crypto_mod
crypto_mod._test_time_cost = 1
crypto_mod._test_memory_cost = 1024

PEPPER = b"test-pepper-32-bytes-exactly!!!!!"
PASSWORD = "test-password-secure-123"


class TestDeriveKey:
    def test_derives_32_byte_key(self):
        salt = generate_salt()
        key = derive_key(PASSWORD, salt, PEPPER)
        assert isinstance(key, bytearray)
        assert len(key) == 32

    def test_same_inputs_same_key(self):
        salt = generate_salt()
        key1 = derive_key(PASSWORD, salt, PEPPER)
        key2 = derive_key(PASSWORD, salt, PEPPER)
        assert key1 == key2

    def test_different_password_different_key(self):
        salt = generate_salt()
        key1 = derive_key(PASSWORD, salt, PEPPER)
        key2 = derive_key("different-password", salt, PEPPER)
        assert key1 != key2

    def test_different_salt_different_key(self):
        salt1 = generate_salt()
        salt2 = generate_salt()
        key1 = derive_key(PASSWORD, salt1, PEPPER)
        key2 = derive_key(PASSWORD, salt2, PEPPER)
        assert key1 != key2

    def test_different_pepper_different_key(self):
        salt = generate_salt()
        key1 = derive_key(PASSWORD, salt, PEPPER)
        key2 = derive_key(PASSWORD, salt, b"different-pepper-32-bytes!!!!!!!!")
        assert key1 != key2


class TestEncryptDecrypt:
    def test_round_trip(self):
        salt = generate_salt()
        key = derive_key(PASSWORD, salt, PEPPER)
        plaintext = json.dumps({"api_key": "sk-test-1234567890"})
        blob = encrypt_credentials(key, plaintext)
        result = decrypt_credentials(key, blob)
        assert result == plaintext

    def test_wrong_key_raises(self):
        salt = generate_salt()
        key1 = derive_key(PASSWORD, salt, PEPPER)
        key2 = derive_key("wrong-password", salt, PEPPER)
        blob = encrypt_credentials(key1, "secret")
        with pytest.raises(InvalidTag):
            decrypt_credentials(key2, blob)

    def test_different_nonce_each_time(self):
        salt = generate_salt()
        key = derive_key(PASSWORD, salt, PEPPER)
        blob1 = encrypt_credentials(key, "same-plaintext")
        blob2 = encrypt_credentials(key, "same-plaintext")
        assert blob1 != blob2  # different nonce → different ciphertext

    def test_tampered_blob_raises(self):
        salt = generate_salt()
        key = derive_key(PASSWORD, salt, PEPPER)
        blob = encrypt_credentials(key, "secret")
        import base64
        raw = bytearray(base64.b64decode(blob))
        raw[-1] ^= 0xFF  # flip last byte
        tampered = base64.b64encode(bytes(raw)).decode()
        with pytest.raises(InvalidTag):
            decrypt_credentials(key, tampered)


class TestCredentialHint:
    def test_api_key_hint(self):
        hint = generate_credential_hint("anthropic", {"api_key": "sk-ant-1234567890abcd"})
        assert hint == "****abcd"

    def test_short_api_key(self):
        hint = generate_credential_hint("openrouter", {"api_key": "ab"})
        assert hint == "****"

    def test_vertex_service_account(self):
        sa = json.dumps({"client_email": "sa@my-project.iam.gserviceaccount.com"})
        hint = generate_credential_hint("vertex_ai", {"vertex_credentials": sa})
        assert hint == "****@my-project.iam.gserviceaccount.com"

    def test_vertex_invalid_json(self):
        hint = generate_credential_hint("vertex_ai", {"vertex_credentials": "not-json"})
        assert hint == "****"

    def test_missing_api_key(self):
        hint = generate_credential_hint("anthropic", {})
        assert hint == "****"


class TestZeroBuffer:
    def test_zeros_bytearray(self):
        buf = bytearray(b"sensitive-data-here")
        zero_buffer(buf)
        assert all(b == 0 for b in buf)

    def test_preserves_length(self):
        buf = bytearray(32)
        buf[:] = b"x" * 32
        zero_buffer(buf)
        assert len(buf) == 32
