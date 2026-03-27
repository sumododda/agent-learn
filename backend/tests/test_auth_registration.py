"""Tests for registration, OTP verification, and resend endpoints."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.auth import pwd_context
from app.main import app
from app.database import get_session
from app.auth import get_current_user
from app.limiter import limiter
from app.models import Base, User
import app.pending_registration_cache as pending_cache
import app.password_reset_cache as password_reset_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    """Create a fresh in-memory SQLite DB for each test."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    # Auth routes don't use get_current_user (they're public), but override
    # anyway so other routes don't blow up if accidentally hit.
    app.dependency_overrides[get_current_user] = lambda: "test-user-id"
    limiter.enabled = False
    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()
    limiter.enabled = True


@pytest.fixture(autouse=True)
def clear_pending_cache():
    """Ensure the pending cache is clean before and after every test."""
    pending_cache._cache.clear()
    yield
    pending_cache._cache.clear()


@pytest.fixture(autouse=True)
def clear_password_reset_cache():
    password_reset_cache._cache.clear()
    yield
    password_reset_cache._cache.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_REGISTER = {
    "email": "alice@example.com",
    "password": "StrongPass123",
    "turnstile_token": "tok_valid",
}


async def _register(client: AsyncClient, **overrides) -> "httpx.Response":
    """POST /api/auth/register with valid mocks in place."""
    payload = {**VALID_REGISTER, **overrides}
    with (
        patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
        patch("app.routers.auth_routes.send_verification_email"),
    ):
        return await client.post("/api/auth/register", json=payload)


async def _register_and_get_otp(client: AsyncClient, email: str = "alice@example.com") -> str:
    """Register a user and return the plaintext OTP captured from the mock."""
    captured_otp = None

    def capture_email(to_email: str, otp: str) -> None:
        nonlocal captured_otp
        captured_otp = otp

    with (
        patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
        patch("app.routers.auth_routes.send_verification_email", side_effect=capture_email),
    ):
        resp = await client.post("/api/auth/register", json={
            "email": email,
            "password": "StrongPass123",
            "turnstile_token": "tok_valid",
        })
        assert resp.status_code == 200
    return captured_otp


# ===================================================================
# 1. Cache tests (unit tests, no HTTP)
# ===================================================================

class TestPendingCache:
    def test_pending_cache_store_and_get(self):
        """store() creates entry, get() retrieves it."""
        pending_cache.store("a@b.com", "pw_hash", "otp_hash")
        entry = pending_cache.get("a@b.com")
        assert entry is not None
        assert entry.email == "a@b.com"
        assert entry.password_hash == "pw_hash"
        assert entry.otp_hash == "otp_hash"
        assert entry.attempts == 0
        assert entry.resend_count == 0

    def test_pending_cache_ttl(self):
        """Entry expires after TTL."""
        pending_cache.store("a@b.com", "pw_hash", "otp_hash")
        entry = pending_cache.get("a@b.com")
        assert entry is not None

        # Move expires_at into the past
        entry.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert pending_cache.get("a@b.com") is None

    def test_pending_cache_increment_attempts(self):
        """increment_attempts() increments and returns new count."""
        pending_cache.store("a@b.com", "pw_hash", "otp_hash")
        assert pending_cache.increment_attempts("a@b.com") == 1
        assert pending_cache.increment_attempts("a@b.com") == 2
        assert pending_cache.get("a@b.com").attempts == 2

    def test_pending_cache_replace_otp(self):
        """replace_otp() updates hash, resets TTL, bumps resend_count."""
        pending_cache.store("a@b.com", "pw_hash", "old_otp_hash")
        entry = pending_cache.get("a@b.com")

        # Move expires_at close to now so we can detect the TTL reset
        short_expiry = datetime.now(timezone.utc) + timedelta(seconds=10)
        entry.expires_at = short_expiry

        result = pending_cache.replace_otp("a@b.com", "new_otp_hash")
        assert result is True

        updated = pending_cache.get("a@b.com")
        assert updated.otp_hash == "new_otp_hash"
        assert updated.resend_count == 1
        # TTL should have been reset to ~10 minutes from now (> the 10s we set)
        assert updated.expires_at > short_expiry

    def test_pending_cache_replace_otp_max_resends(self):
        """replace_otp() returns False after MAX_RESENDS."""
        pending_cache.store("a@b.com", "pw_hash", "otp_hash")
        for i in range(pending_cache.MAX_RESENDS):
            assert pending_cache.replace_otp("a@b.com", f"otp_{i}") is True
        # Next resend should be blocked
        assert pending_cache.replace_otp("a@b.com", "otp_extra") is False

    def test_pending_cache_get_nonexistent(self):
        """get() returns None for unknown email."""
        assert pending_cache.get("nobody@example.com") is None

    def test_pending_cache_remove(self):
        """remove() deletes the entry."""
        pending_cache.store("a@b.com", "pw_hash", "otp_hash")
        pending_cache.remove("a@b.com")
        assert pending_cache.get("a@b.com") is None

    def test_pending_cache_increment_nonexistent(self):
        """increment_attempts() returns 0 for nonexistent email."""
        assert pending_cache.increment_attempts("nobody@example.com") == 0


# ===================================================================
# 2. Registration endpoint tests
# ===================================================================

class TestRegisterEndpoint:
    async def test_register_valid_turnstile(self, client):
        """Registration with valid Turnstile returns 200 with {message, email}."""
        resp = await _register(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Verification code sent"
        assert data["email"] == "alice@example.com"

    async def test_register_invalid_turnstile(self, client):
        """Registration with invalid Turnstile returns 400."""
        with (
            patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=False),
            patch("app.routers.auth_routes.send_verification_email"),
        ):
            resp = await client.post("/api/auth/register", json=VALID_REGISTER)
        assert resp.status_code == 400
        assert "Turnstile" in resp.json()["detail"]

    async def test_register_duplicate_email(self, client):
        """Registration with existing email returns generic success."""
        # First, complete a full registration so user exists in DB
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })

        # Now try to register again — same 200 response to prevent enumeration
        resp = await _register(client, email="alice@example.com")
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@example.com"

    async def test_register_pending_email_idempotent(self, client):
        """Registration with pending email returns 200 (same response)."""
        resp1 = await _register(client)
        assert resp1.status_code == 200

        # Second registration for same email should return 200 idempotently
        resp2 = await _register(client)
        assert resp2.status_code == 200
        assert resp2.json()["email"] == "alice@example.com"

    async def test_register_invalid_email(self, client):
        """Registration with invalid email returns 422."""
        with (
            patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
            patch("app.routers.auth_routes.send_verification_email"),
        ):
            resp = await client.post("/api/auth/register", json={
                "email": "not-an-email",
                "password": "StrongPass123",
                "turnstile_token": "tok_valid",
            })
        assert resp.status_code == 422

    async def test_register_short_password(self, client):
        """Registration with password < 8 chars returns 422."""
        with (
            patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
            patch("app.routers.auth_routes.send_verification_email"),
        ):
            resp = await client.post("/api/auth/register", json={
                "email": "alice@example.com",
                "password": "short",
                "turnstile_token": "tok_valid",
            })
        assert resp.status_code == 422

    async def test_register_sends_email(self, client):
        """Registration calls send_verification_email with correct args."""
        mock_send = None
        with (
            patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
            patch("app.routers.auth_routes.send_verification_email") as m,
        ):
            mock_send = m
            await client.post("/api/auth/register", json=VALID_REGISTER)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "alice@example.com"
        # Second arg is the OTP string (6 digits)
        assert len(call_args[0][1]) == 6
        assert call_args[0][1].isdigit()


# ===================================================================
# 3. OTP verification tests
# ===================================================================

class TestVerifyOtp:
    async def test_verify_otp_success(self, client):
        """Correct OTP creates user and returns JWT."""
        otp = await _register_and_get_otp(client)
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user_id" in data
        assert data["provider_keys_loaded"] is False

    async def test_verify_otp_wrong_code(self, client):
        """Wrong OTP returns 400 and increments attempts."""
        await _register_and_get_otp(client)
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": "000000",
        })
        assert resp.status_code == 400
        assert "Invalid" in resp.json()["detail"]
        # Attempts should have incremented
        entry = pending_cache.get("alice@example.com")
        assert entry.attempts == 1

    async def test_verify_otp_expired(self, client):
        """Expired pending entry returns 410."""
        await _register_and_get_otp(client)
        # Expire the entry
        entry = pending_cache.get("alice@example.com")
        entry.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": "123456",
        })
        assert resp.status_code == 410

    async def test_verify_otp_max_attempts(self, client):
        """5 wrong attempts then returns 429."""
        await _register_and_get_otp(client)

        # Exhaust all attempts
        for _ in range(pending_cache.MAX_ATTEMPTS):
            resp = await client.post("/api/auth/verify-otp", json={
                "email": "alice@example.com",
                "otp": "000000",
            })

        # The 5th attempt should be 400 (wrong code), but the entry now has 5 attempts.
        # The next attempt should be 429.
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": "000000",
        })
        assert resp.status_code == 429
        assert "Too many" in resp.json()["detail"]

    async def test_verify_otp_no_pending(self, client):
        """Verify with no pending registration returns 410."""
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "nobody@example.com",
            "otp": "123456",
        })
        assert resp.status_code == 410

    async def test_verify_otp_clears_cache(self, client):
        """Successful verification removes the pending cache entry."""
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })
        assert pending_cache.get("alice@example.com") is None

    async def test_verify_otp_invalid_format(self, client):
        """OTP with non-digit characters returns 422."""
        await _register_and_get_otp(client)
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": "abc123",
        })
        assert resp.status_code == 422


# ===================================================================
# 4. Resend OTP tests
# ===================================================================

class TestResendOtp:
    async def test_resend_otp_success(self, client):
        """Resend generates new OTP and sends email."""
        await _register_and_get_otp(client)

        with patch("app.routers.auth_routes.send_verification_email") as mock_send:
            resp = await client.post("/api/auth/resend-otp", json={
                "email": "alice@example.com",
            })
        assert resp.status_code == 200
        assert "code has been sent" in resp.json()["message"]
        mock_send.assert_called_once()
        # OTP arg should be 6 digits
        sent_otp = mock_send.call_args[0][1]
        assert len(sent_otp) == 6 and sent_otp.isdigit()

    async def test_resend_otp_limit(self, client):
        """4th resend returns 200 but does not send email (anti-enumeration)."""
        await _register_and_get_otp(client)

        for i in range(pending_cache.MAX_RESENDS):
            with patch("app.routers.auth_routes.send_verification_email"):
                resp = await client.post("/api/auth/resend-otp", json={
                    "email": "alice@example.com",
                })
                assert resp.status_code == 200, f"Resend #{i+1} should succeed"

        # Next resend returns same 200 (anti-enumeration) but email is NOT sent
        with patch("app.routers.auth_routes.send_verification_email") as mock_send:
            resp = await client.post("/api/auth/resend-otp", json={
                "email": "alice@example.com",
            })
        assert resp.status_code == 200
        mock_send.assert_not_called()

    async def test_resend_otp_no_pending(self, client):
        """Resend with no pending registration returns 200 (anti-enumeration)."""
        resp = await client.post("/api/auth/resend-otp", json={
            "email": "nobody@example.com",
        })
        assert resp.status_code == 200

    async def test_resend_updates_otp(self, client):
        """After resend, the old OTP should no longer work but the new one should."""
        old_otp = await _register_and_get_otp(client)

        # Resend and capture new OTP
        new_otp = None
        def capture(email, otp):
            nonlocal new_otp
            new_otp = otp

        with patch("app.routers.auth_routes.send_verification_email", side_effect=capture):
            await client.post("/api/auth/resend-otp", json={
                "email": "alice@example.com",
            })

        # Old OTP should fail (almost certainly different)
        if old_otp != new_otp:
            resp = await client.post("/api/auth/verify-otp", json={
                "email": "alice@example.com",
                "otp": old_otp,
            })
            assert resp.status_code == 400

        # New OTP should succeed
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": new_otp,
        })
        assert resp.status_code == 200
        assert "token" in resp.json()


# ===================================================================
# 5. Integration test
# ===================================================================

class TestFullFlow:
    async def test_full_registration_flow(self, client):
        """Full flow: register -> verify OTP -> use JWT to access protected endpoint."""
        # Step 1: Register
        captured_otp = None
        def capture(email, otp):
            nonlocal captured_otp
            captured_otp = otp

        with (
            patch("app.routers.auth_routes.verify_turnstile_token", new_callable=AsyncMock, return_value=True),
            patch("app.routers.auth_routes.send_verification_email", side_effect=capture),
        ):
            resp = await client.post("/api/auth/register", json={
                "email": "bob@example.com",
                "password": "SecurePass99",
                "turnstile_token": "tok_valid",
            })
        assert resp.status_code == 200
        assert captured_otp is not None

        # Step 2: Verify OTP
        resp = await client.post("/api/auth/verify-otp", json={
            "email": "bob@example.com",
            "otp": captured_otp,
        })
        assert resp.status_code == 200
        data = resp.json()
        token = data["token"]
        user_id = data["user_id"]
        assert token
        assert user_id

        # Step 3: Use JWT to hit a protected endpoint (providers list)
        # Override get_current_user to actually verify the JWT
        del app.dependency_overrides[get_current_user]
        resp = await client.get("/api/providers", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200

    async def test_login_after_registration(self, client):
        """Register + verify -> login with same credentials returns JWT."""
        otp = await _register_and_get_otp(client, email="carol@example.com")
        await client.post("/api/auth/verify-otp", json={
            "email": "carol@example.com",
            "otp": otp,
        })

        # Login
        resp = await client.post("/api/auth/login", json={
            "email": "carol@example.com",
            "password": "StrongPass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user_id" in data


class TestForgotPassword:
    async def test_forgot_password_existing_email_sends_code(self, client):
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })

        with patch("app.routers.auth_routes.send_password_reset_email") as mock_send:
            resp = await client.post("/api/auth/forgot-password", json={
                "email": "alice@example.com",
            })

        assert resp.status_code == 200
        assert "reset code has been sent" in resp.json()["message"]
        mock_send.assert_called_once()
        sent_otp = mock_send.call_args[0][1]
        assert len(sent_otp) == 6 and sent_otp.isdigit()

    async def test_forgot_password_unknown_email_returns_generic_success(self, client):
        with patch("app.routers.auth_routes.send_password_reset_email") as mock_send:
            resp = await client.post("/api/auth/forgot-password", json={
                "email": "nobody@example.com",
            })

        assert resp.status_code == 200
        assert "reset code has been sent" in resp.json()["message"]
        mock_send.assert_not_called()

    async def test_forgot_password_limits_repeat_resends(self, client):
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })

        with patch("app.routers.auth_routes.send_password_reset_email") as mock_send:
            for _ in range(password_reset_cache.MAX_RESENDS + 2):
                resp = await client.post("/api/auth/forgot-password", json={
                    "email": "alice@example.com",
                })
                assert resp.status_code == 200

        assert mock_send.call_count == password_reset_cache.MAX_RESENDS + 1

    async def test_confirm_forgot_password_updates_password(self, client):
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })

        captured_otp = None

        def capture_email(to_email: str, reset_otp: str) -> None:
            nonlocal captured_otp
            captured_otp = reset_otp

        with patch("app.routers.auth_routes.send_password_reset_email", side_effect=capture_email):
            request_resp = await client.post("/api/auth/forgot-password", json={
                "email": "alice@example.com",
            })
        assert request_resp.status_code == 200
        assert captured_otp is not None

        confirm_resp = await client.post("/api/auth/forgot-password/confirm", json={
            "email": "alice@example.com",
            "otp": captured_otp,
            "new_password": "NewStrongPass1",
        })
        assert confirm_resp.status_code == 200
        assert "Password reset successful" in confirm_resp.json()["message"]

        login_resp = await client.post("/api/auth/login", json={
            "email": "alice@example.com",
            "password": "NewStrongPass1",
        })
        assert login_resp.status_code == 200

    async def test_confirm_forgot_password_wrong_code(self, client):
        otp = await _register_and_get_otp(client)
        await client.post("/api/auth/verify-otp", json={
            "email": "alice@example.com",
            "otp": otp,
        })

        with patch("app.routers.auth_routes.send_password_reset_email"):
            await client.post("/api/auth/forgot-password", json={
                "email": "alice@example.com",
            })

        resp = await client.post("/api/auth/forgot-password/confirm", json={
            "email": "alice@example.com",
            "otp": "000000",
            "new_password": "NewStrongPass1",
        })
        assert resp.status_code == 400
        assert "Invalid reset code" in resp.json()["detail"]
