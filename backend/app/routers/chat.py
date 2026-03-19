"""Chat router: model listing, streaming chat, and message history."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app import chat_service
from app.auth import get_current_user
from app.database import SessionDep
from app.models import ChatMessage
from app.schemas import ChatMessageResponse, ChatModelInfo, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Model listing (no auth required)
# ---------------------------------------------------------------------------


@router.get("/chat/models", response_model=list[ChatModelInfo])
async def list_models():
    """Return available text-to-text models from OpenRouter."""
    models = await chat_service.get_models()
    return models


# ---------------------------------------------------------------------------
# Streaming chat endpoint
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/chat")
async def chat_stream(
    course_id: str,
    body: ChatRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Stream a chat completion for a course.

    Persists the user message immediately, streams the assistant response,
    then persists the assistant message after the stream completes.
    """
    # Persist user message
    user_msg = ChatMessage(
        course_id=uuid.UUID(course_id),
        user_id=user_id,
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
        gen, collected = await chat_service.stream_chat(body.model, messages)
        try:
            async for chunk in gen:
                yield chunk
        finally:
            # Persist assistant response in a fresh session – the request
            # session may already be closed by the time the stream finishes.
            full_content = "".join(collected)
            if full_content:
                from app.database import async_session

                async with async_session() as persist_session:
                    assistant_msg = ChatMessage(
                        course_id=uuid.UUID(course_id),
                        user_id=user_id,
                        role="assistant",
                        content=full_content,
                        model=body.model,
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
    cid = uuid.UUID(course_id)

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
