"""Chat service: model listing, context assembly, and streaming via LiteLLM."""

import json
import logging
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import provider_service
from app.models import Blackboard, ChatMessage, Course, EvidenceCard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


async def get_models(
    provider: str,
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> list[dict]:
    """Return available models for the given provider."""
    return await provider_service.list_models(provider, credentials, extra_fields)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


async def assemble_context(
    course_id: str,
    section_context: int,
    user_id: str,
    session: AsyncSession,
) -> list[dict]:
    """Build the message list (system + history) for the chat model.

    The latest user message should already be persisted in the DB before
    calling this function; it will be included in the returned history.
    """
    cid = UUID(course_id)

    # Load course with sections
    result = await session.execute(
        select(Course).options(selectinload(Course.sections)).where(Course.id == cid)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")

    # Load blackboard
    bb_result = await session.execute(
        select(Blackboard).where(Blackboard.course_id == cid)
    )
    blackboard = bb_result.scalar_one_or_none()

    # Load evidence cards for the current section
    ev_result = await session.execute(
        select(EvidenceCard).where(
            EvidenceCard.course_id == cid,
            EvidenceCard.section_position == section_context,
            EvidenceCard.verified == True,  # noqa: E712
        )
    )
    evidence_cards = ev_result.scalars().all()

    # Load last 20 chat messages for this user + course
    msg_result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.course_id == cid,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
    )
    recent_messages = list(reversed(msg_result.scalars().all()))

    # Find current section
    current_section = None
    for s in course.sections:
        if s.position == section_context:
            current_section = s
            break

    section_title = current_section.title if current_section else "Unknown"

    # Build system prompt
    parts: list[str] = []
    parts.append(
        f'You are a learning assistant for a course on "{course.topic}".'
    )
    parts.append(
        f'The learner is currently reading Section {section_context}: "{section_title}".'
    )
    if course.instructions:
        parts.append(course.instructions)

    # Course outline
    parts.append("\n--- COURSE OUTLINE ---")
    for s in course.sections:
        parts.append(f"Section {s.position}: {s.title} - {s.summary}")

    # Current section content
    parts.append("\n--- CURRENT SECTION CONTENT ---")
    if current_section and current_section.content:
        parts.append(current_section.content)
    else:
        parts.append("(content not yet generated)")

    # Blackboard glossary
    parts.append("\n--- KEY TERMS (Blackboard) ---")
    if blackboard and blackboard.glossary:
        for term, definition in blackboard.glossary.items():
            parts.append(f"- {term}: {definition}")
    else:
        parts.append("(no glossary terms yet)")

    # Evidence cards
    parts.append("\n--- EVIDENCE FOR THIS SECTION ---")
    if evidence_cards:
        for ec in evidence_cards:
            parts.append(
                f"- Claim: {ec.claim} | Source: {ec.source_title} | Confidence: {ec.confidence}"
            )
    else:
        parts.append("(no verified evidence cards)")

    system_prompt = "\n".join(parts)

    # Build message list
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    for msg in recent_messages:
        messages.append({"role": msg.role, "content": msg.content})

    return messages


# ---------------------------------------------------------------------------
# Streaming chat via LiteLLM
# ---------------------------------------------------------------------------


async def stream_chat(
    provider: str,
    model: str,
    messages: list[dict],
    credentials: dict,
    extra_fields: dict | None = None,
) -> tuple[AsyncGenerator[bytes, None], list[str]]:
    """Stream a chat completion via LiteLLM provider_service.

    Returns a tuple of (async byte generator, collected_content list).
    The collected_content list is populated as the stream progresses and
    can be read after the generator is exhausted.
    """
    collected_content: list[str] = []

    async def generate() -> AsyncGenerator[bytes, None]:
        try:
            response = await provider_service.stream_completion(
                provider, model, messages, credentials, extra_fields
            )
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                text = delta.content if delta and delta.content else ""
                if text:
                    collected_content.append(text)
                chunk_data = {"choices": [{"delta": {"content": text}}]}
                yield f"data: {json.dumps(chunk_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return generate(), collected_content
