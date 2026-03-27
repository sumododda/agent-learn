"""Tests for JWT authentication flow.

Tests cover:
- Register + verify: creates user via OTP flow, returns JWT and user_id
- Login: authenticates user, returns JWT and user_id
- Protected endpoint with valid token returns 200
- Protected endpoint without token returns 401
- Duplicate email registration returns 409
- Bad credentials return 401
"""

import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.config import settings
from app.database import get_session
from app.models import Base
import app.pending_registration_cache as pending_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_JWT_SECRET = "test-jwt-secret-key-for-auth-tests"


@pytest.fixture
async def auth_db():
    """Create a fresh in-memory DB with NO auth override so real JWT
    verification runs.  Patches JWT_SECRET_KEY so tokens we create via
    the register/login endpoints can be verified."""
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

    original_secret = settings.JWT_SECRET_KEY
    settings.JWT_SECRET_KEY = TEST_JWT_SECRET

    # Clear ALL overrides (including autouse from conftest), then only
    # install the session override -- get_current_user is NOT overridden.
    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    pending_cache._cache.clear()
    yield session_factory

    pending_cache._cache.clear()
    settings.JWT_SECRET_KEY = original_secret
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    """HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_captured_otp = {}


async def _register_user(client: AsyncClient, email: str = "test@example.com", password: str = "SecurePass123"):
    """Register a user via the two-phase OTP flow and return the verify-otp response (with JWT).

    Mocks Turnstile (always passes) and captures the OTP from the email service.
    """
    def capture_email(to_email, otp):
        _captured_otp[to_email] = otp

    with patch("app.routers.auth_routes.verify_turnstile_token", return_value=True), \
         patch("app.routers.auth_routes.send_verification_email", side_effect=capture_email):
        reg_resp = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "turnstile_token": "test-token"},
        )
        assert reg_resp.status_code == 200, f"Register failed: {reg_resp.json()}"

    otp = _captured_otp.get(email)
    assert otp is not None, f"OTP not captured for {email}"

    verify_resp = await client.post(
        "/api/auth/verify-otp",
        json={"email": email, "otp": otp},
    )
    return verify_resp


async def _login_user(client: AsyncClient, email: str = "test@example.com", password: str = "SecurePass123"):
    """Log in a user and return the response."""
    return await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )


# ---------------------------------------------------------------------------
# Tests: Register
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_success(auth_db, client):
    """POST /api/auth/register creates user and returns JWT + user_id."""
    resp = await _register_user(client)
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert "user_id" in data
    assert len(data["token"]) > 0
    assert len(data["user_id"]) > 0


@pytest.mark.anyio
async def test_register_duplicate_email(auth_db, client):
    """POST /api/auth/register with existing email returns generic success."""
    resp1 = await _register_user(client, email="dup@example.com")
    assert resp1.status_code == 200

    with patch("app.routers.auth_routes.verify_turnstile_token", return_value=True), \
         patch("app.routers.auth_routes.send_verification_email"):
        resp2 = await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "SecurePass123", "turnstile_token": "test"},
        )
    assert resp2.status_code == 200
    assert resp2.json()["email"] == "dup@example.com"


# ---------------------------------------------------------------------------
# Tests: Login
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_login_success(auth_db, client):
    """POST /api/auth/login with valid credentials returns JWT + user_id."""
    await _register_user(client, email="login@example.com", password="MyPass123")

    resp = await _login_user(client, email="login@example.com", password="MyPass123")
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert "user_id" in data
    assert len(data["token"]) > 0


@pytest.mark.anyio
async def test_login_bad_password(auth_db, client):
    """POST /api/auth/login with wrong password returns 401."""
    await _register_user(client, email="badpass@example.com", password="RightPass1")

    resp = await _login_user(client, email="badpass@example.com", password="WrongPass1")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_nonexistent_email(auth_db, client):
    """POST /api/auth/login with unknown email returns 401."""
    resp = await _login_user(client, email="nobody@example.com", password="whatever")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: Protected endpoint with token
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_protected_endpoint_with_valid_token(auth_db, client):
    """GET /api/courses with a valid JWT returns 200."""
    reg_resp = await _register_user(client, email="authed@example.com")
    token = reg_resp.json()["token"]

    resp = await client.get(
        "/api/courses",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Protected endpoint without token
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_protected_endpoint_without_token(auth_db, client):
    """GET /api/courses without Authorization header returns 401."""
    resp = await client.get("/api/courses")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_protected_endpoint_with_invalid_token(auth_db, client):
    """GET /api/courses with a garbage token returns 401."""
    resp = await client.get(
        "/api/courses",
        headers={"Authorization": "Bearer not.a.valid.jwt.token"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_protected_endpoint_with_no_bearer_prefix(auth_db, client):
    """GET /api/courses with token but no 'Bearer ' prefix returns 401."""
    reg_resp = await _register_user(client, email="nobearer@example.com")
    token = reg_resp.json()["token"]

    resp = await client.get(
        "/api/courses",
        headers={"Authorization": token},  # missing "Bearer " prefix
    )
    assert resp.status_code == 401
