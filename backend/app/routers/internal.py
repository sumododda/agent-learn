"""Internal API endpoints for Trigger.dev pipeline orchestration.

These endpoints are called by Trigger.dev tasks, not by the frontend.
All endpoints require a valid X-Internal-Token header.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException

from app.agent_service import (
    run_discover_and_plan,
    run_edit_section,
    run_research_section,
    run_verify_section,
    run_write_section,
)
from app.config import settings
from app.database import SessionDep
from app.schemas import (
    DiscoverAndPlanResponse,
    EditSectionResponse,
    InternalCourseRequest,
    InternalSectionRequest,
    ResearchSectionResponse,
    VerifySectionResponse,
    WriteSectionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Token verification dependency
# ---------------------------------------------------------------------------


async def verify_internal_token(
    x_internal_token: str = Header(default=None),
) -> None:
    """Verify the X-Internal-Token header against the configured secret.

    Raises 401 if the token is missing, empty, or does not match.
    """
    if not settings.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="INTERNAL_API_TOKEN is not configured on the server",
        )
    if not x_internal_token or x_internal_token != settings.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing internal token",
        )


# ---------------------------------------------------------------------------
# Internal endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/internal/discover-and-plan",
    response_model=DiscoverAndPlanResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def discover_and_plan(body: InternalCourseRequest, session: SessionDep):
    """Run discovery research + planning for a course.

    Reads course from DB, runs discovery agent, runs planner agent,
    creates sections + research briefs in DB, returns them.
    """
    try:
        course_id = uuid.UUID(body.course_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course_id format")

    try:
        result = await run_discover_and_plan(course_id, session)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("discover-and-plan failed for course %s: %s", body.course_id, e)
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post(
    "/internal/research-section",
    response_model=ResearchSectionResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def research_section(body: InternalSectionRequest, session: SessionDep):
    """Run section researcher for one section.

    Reads research brief from DB, runs researcher agent, saves
    evidence cards to DB, returns them.
    """
    try:
        course_id = uuid.UUID(body.course_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course_id format")

    try:
        result = await run_research_section(
            course_id, body.section_position, session
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "research-section failed for course %s section %s: %s",
            body.course_id,
            body.section_position,
            e,
        )
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post(
    "/internal/verify-section",
    response_model=VerifySectionResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def verify_section(body: InternalSectionRequest, session: SessionDep):
    """Run verifier for one section.

    Reads evidence cards from DB, runs verifier agent, updates
    verification status, returns result.
    """
    try:
        course_id = uuid.UUID(body.course_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course_id format")

    try:
        result = await run_verify_section(
            course_id, body.section_position, session
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "verify-section failed for course %s section %s: %s",
            body.course_id,
            body.section_position,
            e,
        )
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post(
    "/internal/write-section",
    response_model=WriteSectionResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def write_section_endpoint(body: InternalSectionRequest, session: SessionDep):
    """Run writer for one section.

    Reads evidence cards + blackboard from DB, runs writer agent,
    saves content + citations to section, returns them.
    """
    try:
        course_id = uuid.UUID(body.course_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course_id format")

    try:
        result = await run_write_section(
            course_id, body.section_position, session
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "write-section failed for course %s section %s: %s",
            body.course_id,
            body.section_position,
            e,
        )
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post(
    "/internal/edit-section",
    response_model=EditSectionResponse,
    dependencies=[Depends(verify_internal_token)],
)
async def edit_section_endpoint(body: InternalSectionRequest, session: SessionDep):
    """Run editor for one section.

    Reads draft + blackboard + evidence from DB, runs editor agent,
    updates content + blackboard, returns result.
    """
    try:
        course_id = uuid.UUID(body.course_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid course_id format")

    try:
        result = await run_edit_section(
            course_id, body.section_position, session
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "edit-section failed for course %s section %s: %s",
            body.course_id,
            body.section_position,
            e,
        )
        raise HTTPException(status_code=500, detail=str(e))

    return result
