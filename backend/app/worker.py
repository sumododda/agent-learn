"""Standalone worker process that claims pipeline jobs from the queue and runs them.

Usage::

    python -m app.worker

The worker loops continuously, claiming pending jobs from the ``pipeline_jobs``
table, resolving encrypted credentials, and delegating to ``run_pipeline``.
Graceful shutdown on SIGTERM/SIGINT, stale-job recovery every ~60 s.
"""

import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update

from app.config import settings
from app.crypto import decrypt_credentials, derive_key
from app.database import async_session
from app.models import Course, PipelineJob, ProviderConfig, UserKeySalt
from app.pipeline import run_pipeline, update_job_status

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
STALE_THRESHOLD_SECONDS = 120  # 2 minutes
POLL_INTERVAL = 2  # seconds between claim attempts
STALE_CHECK_EVERY = 30  # iterations (30 * 2s = ~60s)


# ---------------------------------------------------------------------------
# Job claiming (Postgres FOR UPDATE SKIP LOCKED)
# ---------------------------------------------------------------------------


async def claim_next_job(session, worker_id: str) -> PipelineJob | None:
    """Atomically claim the oldest pending job that hasn't exceeded max_attempts.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple workers never claim the same row.
    Returns the full PipelineJob or None if no jobs are available.
    """
    result = await session.execute(
        text(
            "UPDATE pipeline_jobs "
            "SET status='claimed', worker_id=:wid, started_at=now(), "
            "    heartbeat_at=now(), attempts=attempts+1 "
            "WHERE id = ("
            "  SELECT id FROM pipeline_jobs "
            "  WHERE status='pending' AND attempts < max_attempts "
            "  ORDER BY created_at "
            "  FOR UPDATE SKIP LOCKED "
            "  LIMIT 1"
            ") RETURNING id"
        ),
        {"wid": worker_id},
    )
    row = result.fetchone()
    if row is None:
        return None

    job_id = row[0]
    await session.commit()

    # Reload the full ORM object
    job_result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job_id)
    )
    return job_result.scalar_one()


# ---------------------------------------------------------------------------
# Stale recovery
# ---------------------------------------------------------------------------


async def mark_stale_jobs(session) -> int:
    """Find running jobs whose heartbeat is older than 2 minutes and mark them stale.

    Also updates the associated Course status to 'stale'.
    Returns the count of stale jobs found.
    """
    cutoff = text("now() - interval '2 minutes'")

    # Find stale jobs
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
        # Also update the course status
        await session.execute(
            update(Course)
            .where(Course.id == job.course_id)
            .values(status="stale")
        )

    await session.commit()

    count = len(stale_jobs)
    logger.info("Marked %d stale job(s)", count)
    return count


# ---------------------------------------------------------------------------
# Heartbeat background task
# ---------------------------------------------------------------------------


async def _heartbeat_loop(job_id: uuid.UUID, stop_event: asyncio.Event) -> None:
    """Update heartbeat_at every 30 seconds until stop_event is set."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL)
            # stop_event was set — exit
            break
        except asyncio.TimeoutError:
            pass  # Timeout means we should send a heartbeat

        try:
            async with async_session() as session:
                await session.execute(
                    update(PipelineJob)
                    .where(PipelineJob.id == job_id)
                    .values(heartbeat_at=datetime.now(timezone.utc))
                )
                await session.commit()
        except Exception:
            logger.warning("Heartbeat update failed for job %s", job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


async def _resolve_credentials(job: PipelineJob) -> tuple[dict, dict | None]:
    """Read ProviderConfig + UserKeySalt, decrypt, and return (creds, search_creds).

    Returns a tuple of (llm_credentials_dict, search_credentials_dict_or_None).
    """
    pepper = settings.ENCRYPTION_PEPPER.encode()

    async with async_session() as session:
        # Fetch the user's key salt
        salt_result = await session.execute(
            select(UserKeySalt).where(UserKeySalt.user_id == job.user_id)
        )
        salt_row = salt_result.scalar_one()
        key = derive_key(salt_row.salt, pepper)

        # Fetch LLM provider config
        provider_name = job.config.get("provider", "")
        provider_result = await session.execute(
            select(ProviderConfig).where(
                ProviderConfig.user_id == job.user_id,
                ProviderConfig.provider == provider_name,
            )
        )
        provider_row = provider_result.scalar_one()
        creds = json.loads(decrypt_credentials(key, provider_row.encrypted_credentials))

        # Fetch search provider config if configured
        search_creds = None
        search_provider = job.config.get("search_provider")
        if search_provider:
            search_result = await session.execute(
                select(ProviderConfig).where(
                    ProviderConfig.user_id == job.user_id,
                    ProviderConfig.provider == search_provider,
                )
            )
            search_row = search_result.scalar_one()
            search_creds = json.loads(
                decrypt_credentials(key, search_row.encrypted_credentials)
            )

    return creds, search_creds


# ---------------------------------------------------------------------------
# Job processing
# ---------------------------------------------------------------------------


async def process_job(job: PipelineJob, shutdown_event: asyncio.Event) -> None:
    """Process a single pipeline job: heartbeat, resolve creds, run pipeline."""
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(job.id, heartbeat_stop))

    try:
        # Resolve credentials
        creds, search_creds = await _resolve_credentials(job)

        # Mark job as running
        async with async_session() as session:
            await session.execute(
                update(PipelineJob)
                .where(PipelineJob.id == job.id)
                .values(status="running")
            )
            await session.commit()

        # Run the pipeline
        result = await run_pipeline(
            job_id=job.id,
            course_id=job.course_id,
            checkpoint=job.checkpoint,
            provider=job.config.get("provider", ""),
            model=job.config.get("model", ""),
            credentials=creds,
            extra_fields=job.config.get("extra_fields"),
            search_provider=job.config.get("search_provider", ""),
            search_credentials=search_creds,
            shutdown_event=shutdown_event,
        )

        # If pipeline returned "pending" (graceful shutdown), set job back to pending
        if result == "pending":
            async with async_session() as session:
                await update_job_status(job.id, "pending", session)
            logger.info("Job %s set back to pending (graceful shutdown)", job.id)
        elif result == "cancelled":
            logger.info("Job %s cancelled (course deleted)", job.id)

        # Otherwise, run_pipeline handles its own final status

    except Exception as exc:
        logger.error("Job %s failed with exception: %s", job.id, exc, exc_info=True)
        try:
            sanitized_error = "Pipeline failed. Check server logs for details."
            async with async_session() as session:
                await update_job_status(
                    job.id, "failed", session, error=sanitized_error
                )
            async with async_session() as session:
                await session.execute(
                    update(Course)
                    .where(Course.id == job.course_id)
                    .values(status="failed")
                )
                await session.commit()
        except Exception:
            logger.error(
                "Failed to update status for job %s after error",
                job.id,
                exc_info=True,
            )

    finally:
        heartbeat_stop.set()
        await heartbeat_task


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------


async def run_worker() -> None:
    """Main worker loop: claim jobs, process them, handle shutdown."""
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("Shutdown signal received — finishing current job...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info("Worker %s starting", worker_id)

    # Initial stale recovery
    async with async_session() as session:
        stale_count = await mark_stale_jobs(session)
    if stale_count:
        logger.info("Recovered %d stale job(s) on startup", stale_count)

    iteration = 0

    while not shutdown_event.is_set():
        # Periodic stale recovery
        if iteration > 0 and iteration % STALE_CHECK_EVERY == 0:
            try:
                async with async_session() as session:
                    await mark_stale_jobs(session)
            except Exception:
                logger.warning("Stale recovery failed", exc_info=True)

        # Try to claim a job
        job = None
        try:
            async with async_session() as session:
                job = await claim_next_job(session, worker_id)
        except Exception:
            logger.warning("Failed to claim job", exc_info=True)

        if job is None:
            # No work — sleep briefly then retry
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=POLL_INTERVAL
                )
                # shutdown_event was set
                break
            except asyncio.TimeoutError:
                pass
            iteration += 1
            continue

        logger.info(
            "Claimed job %s (course=%s, attempt=%d/%d, checkpoint=%d)",
            job.id,
            job.course_id,
            job.attempts,
            job.max_attempts,
            job.checkpoint,
        )

        await process_job(job, shutdown_event)
        iteration += 1

    logger.info("Worker %s shutting down", worker_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Configure logging (same as main.py)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langsmith").setLevel(logging.WARNING)

    asyncio.run(run_worker())
