"""Chat service: context assembly and streaming via OpenRouter."""

import json
import logging
from collections.abc import AsyncGenerator
from uuid import UUID

from langchain_openai import ChatOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.provider_service import OPENROUTER_BASE
from app.models import Blackboard, ChatMessage, Course, EvidenceCard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


async def assemble_context(
    course_id: str,
    section_context: int,
    user_id: str,
    session: AsyncSession,
) -> list[dict]:
    """Build the message list (system + history) for the chat model."""
    cid = UUID(course_id)

    result = await session.execute(
        select(Course).options(selectinload(Course.sections)).where(Course.id == cid)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")

    bb_result = await session.execute(
        select(Blackboard).where(Blackboard.course_id == cid)
    )
    blackboard = bb_result.scalar_one_or_none()

    ev_result = await session.execute(
        select(EvidenceCard).where(
            EvidenceCard.course_id == cid,
            EvidenceCard.section_position == section_context,
            EvidenceCard.verified == True,  # noqa: E712
        )
    )
    evidence_cards = ev_result.scalars().all()

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

    current_section = None
    for s in course.sections:
        if s.position == section_context:
            current_section = s
            break

    section_title = current_section.title if current_section else "Unknown"

    parts: list[str] = []
    parts.append(f'You are a learning assistant for a course on "{course.topic}".')
    parts.append(f'The learner is currently reading Section {section_context}: "{section_title}".')
    if course.instructions:
        parts.append(course.instructions)

    parts.append("\n--- COURSE OUTLINE ---")
    for s in course.sections:
        parts.append(f"Section {s.position}: {s.title} - {s.summary}")

    parts.append("\n--- CURRENT SECTION CONTENT ---")
    if current_section and current_section.content:
        parts.append(current_section.content)
    else:
        parts.append("(content not yet generated)")

    parts.append("\n--- KEY TERMS (Blackboard) ---")
    if blackboard and blackboard.glossary:
        for term, definition in blackboard.glossary.items():
            parts.append(f"- {term}: {definition}")
    else:
        parts.append("(no glossary terms yet)")

    parts.append("\n--- EVIDENCE FOR THIS SECTION ---")
    if evidence_cards:
        for ec in evidence_cards:
            parts.append(
                f"- Claim: {ec.claim} | Source: {ec.source_title} | Confidence: {ec.confidence}"
            )
    else:
        parts.append("(no verified evidence cards)")

    system_prompt = "\n".join(parts)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in recent_messages:
        messages.append({"role": msg.role, "content": msg.content})

    return messages


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------


async def stream_chat(
    model: str,
    messages: list[dict],
    api_key: str,
) -> tuple[AsyncGenerator[bytes, None], list[str]]:
    """Stream a chat completion via OpenRouter using LangChain.

    Returns (async byte generator, collected_content list).
    """
    collected_content: list[str] = []

    async def generate() -> AsyncGenerator[bytes, None]:
        try:
            llm = ChatOpenAI(
                base_url=OPENROUTER_BASE,
                api_key=api_key,
                model=model,
                streaming=True,
            )
            lc_messages = [
                {"role": m["role"], "content": m["content"]} for m in messages
            ]
            async for chunk in llm.astream(lc_messages):
                text = chunk.content if chunk.content else ""
                if text:
                    collected_content.append(text)
                chunk_data = {"choices": [{"delta": {"content": text}}]}
                yield f"data: {json.dumps(chunk_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as e:
            logger.error("Stream error: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return generate(), collected_content
