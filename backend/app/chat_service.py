"""Chat service: OpenRouter model listing, context assembly, and streaming."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Blackboard, ChatMessage, Course, EvidenceCard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model listing with 5-minute TTL cache
# ---------------------------------------------------------------------------

_models_cache: list[dict] | None = None
_models_cache_time: float = 0
_MODELS_TTL: float = 300.0  # 5 minutes
_models_lock = asyncio.Lock()


async def get_models() -> list[dict]:
    """Fetch text-to-text models from OpenRouter, cached for 5 minutes."""
    global _models_cache, _models_cache_time
    now = time.time()
    if _models_cache is not None and (now - _models_cache_time) < _MODELS_TTL:
        return _models_cache

    async with _models_lock:
        # Double-check after acquiring lock
        now = time.time()
        if _models_cache is not None and (now - _models_cache_time) < _MODELS_TTL:
            return _models_cache

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

        models: list[dict] = []
        for m in data.get("data", []):
            arch = m.get("architecture") or {}
            input_mods = arch.get("input_modalities") or []
            output_mods = arch.get("output_modalities") or []
            if "text" in input_mods and "text" in output_mods:
                pricing = m.get("pricing") or {}
                models.append(
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "context_length": m.get("context_length", 0),
                        "pricing_prompt": pricing.get("prompt", "0"),
                        "pricing_completion": pricing.get("completion", "0"),
                    }
                )

        _models_cache = models
        _models_cache_time = time.time()
        return models


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
# Streaming chat via OpenRouter
# ---------------------------------------------------------------------------


async def stream_chat(
    model: str, messages: list[dict]
) -> tuple[AsyncGenerator[bytes, None], list[str]]:
    """Stream a chat completion from OpenRouter.

    Returns a tuple of (async byte generator, collected_content list).
    The collected_content list is populated as the stream progresses and
    can be read after the generator is exhausted.
    """
    collected_content: list[str] = []

    async def generate() -> AsyncGenerator[bytes, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                json={"model": model, "messages": messages, "stream": True},
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    error_body = response.text
                    yield f'data: {{"error": "{error_body}"}}\n\n'.encode()
                    return
                async for chunk in response.aiter_bytes():
                    # Parse content for accumulation
                    try:
                        for line in chunk.decode("utf-8", errors="replace").split("\n"):
                            line = line.strip()
                            if line.startswith("data: ") and line != "data: [DONE]":
                                data = json.loads(line[6:])
                                delta = (
                                    data.get("choices", [{}])[0].get("delta", {})
                                )
                                if "content" in delta:
                                    collected_content.append(delta["content"])
                    except Exception as e:
                        logger.debug("Failed to parse SSE chunk: %s", e)
                    yield chunk

    return generate(), collected_content
