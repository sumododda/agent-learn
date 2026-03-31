"""Chat router: model listing, streaming chat, and message history."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app import chat_service, key_cache, provider_service
from app.auth import get_current_user
from app.database import SessionDep
from app.limiter import limiter
from app.models import ChatMessage, Course, ProviderConfig
from app.schemas import ChatMessageResponse, ChatModelInfo, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


async def _ensure_cache(user_id: str, session) -> None:
    """Lazy-load provider credentials into cache if not already present."""
    if key_cache.get_default(user_id) is not None:
        return
    from app.routers.auth_routes import _load_provider_keys
    uid = uuid.UUID(user_id)
    await _load_provider_keys(user_id, uid, session)


@router.get("/chat/models", response_model=list[ChatModelInfo])
@limiter.limit("30/minute")
async def list_models(
    request: Request,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Return available models for the user's default provider."""
    await _ensure_cache(user_id, session)
    default = key_cache.get_default(user_id)
    if default is None:
        return []
    provider, creds = default
    # Fetch extra_fields from DB
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uuid.UUID(user_id),
            ProviderConfig.provider == provider,
        )
    )
    pc = result.scalar_one_or_none()
    extra_fields = pc.extra_fields if pc else {}
    models = await chat_service.get_models(provider, creds, extra_fields or {})
    return models


# ---------------------------------------------------------------------------
# Streaming chat endpoint
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/chat")
@limiter.limit("30/minute")
async def chat_stream(
    request: Request,
    course_id: str,
    body: ChatRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Stream a chat completion for a course.

    Persists the user message immediately, streams the assistant response,
    then persists the assistant message after the stream completes.
    """
    # Verify course exists and user owns it
    course_result = await session.execute(
        select(Course).where(Course.id == uuid.UUID(course_id))
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Get provider credentials from cache (lazy-load if needed)
    await _ensure_cache(user_id, session)
    default = key_cache.get_default(user_id)
    if default is None:
        raise HTTPException(status_code=400, detail="no_provider_configured")
    provider, creds = default

    # Fetch extra_fields from DB
    pc_result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uuid.UUID(user_id),
            ProviderConfig.provider == provider,
        )
    )
    pc = pc_result.scalar_one_or_none()
    extra_fields = pc.extra_fields if pc else {}
    selected_model = body.model or (extra_fields or {}).get("model") or provider_service.get_default_model(provider)

    # Persist user message
    user_msg = ChatMessage(
        course_id=uuid.UUID(course_id),
        user_id=uuid.UUID(user_id),
        role="user",
        content=body.message,
        model=None,
        section_context=body.section_context,
    )
    session.add(user_msg)
    await session.commit()

    # Build context messages (includes the just-persisted user message from DB)
    messages = await chat_service.assemble_context(
        course_id, body.section_context, user_id, session
    )

    async def wrapper():
        gen, collected = await chat_service.stream_chat(
            provider,
            selected_model,
            messages,
            creds,
            extra_fields,
        )
        try:
            async for chunk in gen:
                yield chunk
        finally:
            # Persist assistant response in a fresh session -- the request
            # session may already be closed by the time the stream finishes.
            full_content = "".join(collected)
            if full_content:
                from app.database import async_session

                async with async_session() as persist_session:
                    assistant_msg = ChatMessage(
                        course_id=uuid.UUID(course_id),
                        user_id=uuid.UUID(user_id),
                        role="assistant",
                        content=full_content,
                        model=selected_model,
                        section_context=body.section_context,
                    )
                    persist_session.add(assistant_msg)
                    await persist_session.commit()

    return StreamingResponse(
        wrapper(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------
# Chat history endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/chat",
    response_model=list[ChatMessageResponse],
)
async def chat_history(
    course_id: str,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
    before: str | None = Query(default=None),
):
    """Return chat history for a course, ordered oldest-first.

    Supports cursor-based pagination via the `before` parameter (a message UUID).
    """
    # Verify course exists and user owns it
    cid = uuid.UUID(course_id)
    course_result = await session.execute(
        select(Course).where(Course.id == cid)
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    query = (
        select(ChatMessage)
        .where(
            ChatMessage.course_id == cid,
            ChatMessage.user_id == user_id,
        )
    )

    # Cursor-based pagination: fetch messages older than the given message
    if before:
        before_id = uuid.UUID(before)
        # Sub-query to get the created_at of the cursor message
        cursor_msg = await session.execute(
            select(ChatMessage.created_at).where(ChatMessage.id == before_id)
        )
        cursor_row = cursor_msg.scalar_one_or_none()
        if cursor_row is not None:
            query = query.where(ChatMessage.created_at < cursor_row)

    query = query.order_by(ChatMessage.created_at.desc()).limit(limit)

    result = await session.execute(query)
    messages = list(reversed(result.scalars().all()))
    return messages
