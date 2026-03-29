import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event

from app.models import Base, PipelineJob, User, Course

TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
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
async def user_and_course(db_session):
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        password_hash="hashed",
    )
    db_session.add(user)
    await db_session.commit()

    course = Course(
        id=uuid.uuid4(),
        topic="Test Course",
        user_id=user.id,
    )
    db_session.add(course)
    await db_session.commit()
    return user, course


@pytest.mark.asyncio
async def test_create_pipeline_job(db_session, user_and_course):
    user, course = user_and_course

    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        config={"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    )
    db_session.add(job)
    await db_session.commit()

    result = await db_session.execute(
        select(PipelineJob).where(PipelineJob.course_id == course.id)
    )
    row = result.scalar_one()

    assert row.id is not None
    assert row.course_id == course.id
    assert row.user_id == user.id
    assert row.config == {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}


@pytest.mark.asyncio
async def test_pipeline_job_defaults(db_session, user_and_course):
    user, course = user_and_course

    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        config={"provider": "openai", "model": "gpt-4o"},
    )
    db_session.add(job)
    await db_session.commit()

    result = await db_session.execute(
        select(PipelineJob).where(PipelineJob.course_id == course.id)
    )
    row = result.scalar_one()

    assert row.status == "pending"
    assert row.checkpoint == 0
    assert row.attempts == 0
    assert row.max_attempts == 2
    assert row.worker_id is None
    assert row.heartbeat_at is None
    assert row.started_at is None
    assert row.completed_at is None
    assert row.error is None
