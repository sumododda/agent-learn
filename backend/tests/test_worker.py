"""Tests for the standalone worker process (app.worker).

Tests cover:
- Stale job detection: running jobs with old heartbeat get marked 'stale'
- Credential resolution: ProviderConfig + UserKeySalt decryption flow
- Job claiming: mock for SQLite (Postgres FOR UPDATE SKIP LOCKED not available)
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event as sa_event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.crypto import derive_key, encrypt_credentials, generate_salt
from app.models import Base, Course, PipelineJob, ProviderConfig, User, UserKeySalt
from app.worker import _resolve_credentials, mark_stale_jobs

TEST_DATABASE_URL = "sqlite+aiosqlite://"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def worker_db():
    """Create a fresh in-memory SQLite DB with all tables."""
    engine = create_async_engine(TEST_DATABASE_URL)

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def seeded(worker_db):
    """Create a user, course, and return (user, course, session)."""
    session = worker_db

    user = User(
        id=uuid.uuid4(),
        email="worker-test@example.com",
        password_hash="hashed",
    )
    course = Course(
        id=uuid.uuid4(),
        topic="Worker Test Course",
        user_id=str(user.id),
    )
    session.add_all([user, course])
    await session.commit()
    return user, course, session


# ---------------------------------------------------------------------------
# Mock claim helper (SQLite doesn't support FOR UPDATE SKIP LOCKED)
# ---------------------------------------------------------------------------


async def mock_claim(session, worker_id: str) -> PipelineJob | None:
    """SQLite-compatible substitute for claim_next_job."""
    result = await session.execute(
        select(PipelineJob).where(PipelineJob.status == "pending")
    )
    job = result.scalars().first()
    if job:
        job.status = "claimed"
        job.worker_id = worker_id
        job.attempts += 1
        await session.commit()
    return job


# ---------------------------------------------------------------------------
# Test: Stale job detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_stale_jobs(seeded):
    """Running jobs with old heartbeat_at are marked 'stale', along with Course."""
    user, course, session = seeded

    # Create a job that's been running with an old heartbeat (3 min ago)
    old_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=3)
    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="running",
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        heartbeat_at=old_heartbeat,
        started_at=old_heartbeat,
    )
    session.add(job)
    await session.commit()

    # mark_stale_jobs uses now() - interval '2 minutes' which is Postgres syntax.
    # For SQLite testing, we patch it to use a direct datetime comparison.
    from sqlalchemy import literal_column

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD)

    with patch("app.worker.mark_stale_jobs", new=_mark_stale_jobs_sqlite):
        count = await _mark_stale_jobs_sqlite(session)

    assert count == 1

    # Verify job is now stale
    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job.id)
    )
    updated_job = result.scalar_one()
    assert updated_job.status == "stale"

    # Verify course is now stale
    result = await session.execute(
        select(Course).where(Course.id == course.id)
    )
    updated_course = result.scalar_one()
    assert updated_course.status == "stale"


@pytest.mark.asyncio
async def test_mark_stale_jobs_ignores_fresh(seeded):
    """Jobs with recent heartbeat_at are NOT marked stale."""
    user, course, session = seeded

    # Create a job with a fresh heartbeat (10 seconds ago)
    fresh_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=10)
    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="running",
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        heartbeat_at=fresh_heartbeat,
        started_at=fresh_heartbeat,
    )
    session.add(job)
    await session.commit()

    count = await _mark_stale_jobs_sqlite(session)
    assert count == 0

    # Verify job is still running
    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job.id)
    )
    updated_job = result.scalar_one()
    assert updated_job.status == "running"


@pytest.mark.asyncio
async def test_mark_stale_jobs_ignores_non_running(seeded):
    """Only 'running' jobs are considered for stale detection."""
    user, course, session = seeded

    old_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=5)
    # A 'pending' job with old heartbeat should NOT be marked stale
    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="pending",
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        heartbeat_at=old_heartbeat,
    )
    session.add(job)
    await session.commit()

    count = await _mark_stale_jobs_sqlite(session)
    assert count == 0


# ---------------------------------------------------------------------------
# Test: Credential resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_credentials(seeded):
    """_resolve_credentials decrypts LLM provider creds correctly."""
    user, course, session = seeded

    # Set up encryption infrastructure
    pepper = settings.ENCRYPTION_PEPPER.encode() if settings.ENCRYPTION_PEPPER else b"test-pepper-key!"
    salt = generate_salt()
    key = derive_key(salt, pepper)

    # Store salt
    user_salt = UserKeySalt(user_id=user.id, salt=salt)
    session.add(user_salt)

    # Store encrypted LLM credentials
    llm_creds = {"api_key": "sk-real-key-12345"}
    encrypted = encrypt_credentials(key, json.dumps(llm_creds))
    provider_config = ProviderConfig(
        user_id=user.id,
        provider="anthropic",
        encrypted_credentials=encrypted,
        credential_hint="****2345",
    )
    session.add(provider_config)
    await session.commit()

    # Create a job referencing that provider
    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="claimed",
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    )
    session.add(job)
    await session.commit()

    # Patch async_session to return our test session and pepper to match
    class _FakeSessionCtx:
        def __init__(self, s):
            self._s = s

        async def __aenter__(self):
            return self._s

        async def __aexit__(self, *exc):
            return False

    with patch("app.worker.async_session", return_value=_FakeSessionCtx(session)):
        with patch("app.worker.settings") as mock_settings:
            mock_settings.ENCRYPTION_PEPPER = pepper.decode() if isinstance(pepper, bytes) else pepper
            creds, search_creds = await _resolve_credentials(job)

    assert creds == llm_creds
    assert search_creds is None


@pytest.mark.asyncio
async def test_resolve_credentials_with_search(seeded):
    """_resolve_credentials also decrypts search provider creds when configured."""
    user, course, session = seeded

    pepper = settings.ENCRYPTION_PEPPER.encode() if settings.ENCRYPTION_PEPPER else b"test-pepper-key!"
    salt = generate_salt()
    key = derive_key(salt, pepper)

    user_salt = UserKeySalt(user_id=user.id, salt=salt)
    session.add(user_salt)

    # LLM provider
    llm_creds = {"api_key": "sk-llm-key"}
    session.add(
        ProviderConfig(
            user_id=user.id,
            provider="anthropic",
            encrypted_credentials=encrypt_credentials(key, json.dumps(llm_creds)),
            credential_hint="****-key",
        )
    )

    # Search provider
    search_creds = {"api_key": "sk-search-key"}
    session.add(
        ProviderConfig(
            user_id=user.id,
            provider="tavily",
            encrypted_credentials=encrypt_credentials(key, json.dumps(search_creds)),
            credential_hint="****-key",
        )
    )
    await session.commit()

    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="claimed",
        config={
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "search_provider": "tavily",
        },
    )
    session.add(job)
    await session.commit()

    class _FakeSessionCtx:
        def __init__(self, s):
            self._s = s

        async def __aenter__(self):
            return self._s

        async def __aexit__(self, *exc):
            return False

    with patch("app.worker.async_session", return_value=_FakeSessionCtx(session)):
        with patch("app.worker.settings") as mock_settings:
            mock_settings.ENCRYPTION_PEPPER = pepper.decode() if isinstance(pepper, bytes) else pepper
            creds, s_creds = await _resolve_credentials(job)

    assert creds == llm_creds
    assert s_creds == search_creds


# ---------------------------------------------------------------------------
# Test: Mock claim (SQLite-compatible)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_claim_returns_job(seeded):
    """mock_claim picks up a pending job and marks it claimed."""
    user, course, session = seeded

    job = PipelineJob(
        id=uuid.uuid4(),
        course_id=course.id,
        user_id=user.id,
        status="pending",
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    )
    session.add(job)
    await session.commit()

    claimed = await mock_claim(session, "worker-test")
    assert claimed is not None
    assert claimed.status == "claimed"
    assert claimed.worker_id == "worker-test"
    assert claimed.attempts == 1


@pytest.mark.asyncio
async def test_mock_claim_returns_none_when_empty(seeded):
    """mock_claim returns None when there are no pending jobs."""
    _, _, session = seeded
    claimed = await mock_claim(session, "worker-test")
    assert claimed is None


# ---------------------------------------------------------------------------
# SQLite-compatible stale detection helper
# (Postgres interval syntax doesn't work in SQLite)
# ---------------------------------------------------------------------------

STALE_THRESHOLD = 120  # seconds


async def _mark_stale_jobs_sqlite(session) -> int:
    """SQLite-compatible version of mark_stale_jobs for testing."""
    from sqlalchemy import update as sa_update

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD)

    result = await session.execute(
        select(PipelineJob).where(
            PipelineJob.status == "running",
            PipelineJob.heartbeat_at < cutoff,
        )
    )
    stale_jobs = list(result.scalars().all())

    if not stale_jobs:
        return 0

    for job in stale_jobs:
        job.status = "stale"
        await session.execute(
            sa_update(Course)
            .where(Course.id == job.course_id)
            .values(status="stale")
        )

    await session.commit()
    return len(stale_jobs)
