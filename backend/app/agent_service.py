import asyncio
import json
import logging
import re
from collections.abc import Callable, Awaitable
from datetime import date
from typing import Sequence

EventCallback = Callable[[str, dict], Awaitable[None]]

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import (
    create_planner,
    create_editor,
    create_discovery_researcher,
    create_section_researcher,
    create_verifier,
    CourseOutlineWithBriefs,
    TopicBrief,
    EvidenceCardItem,
    EvidenceCardSet,
    VerificationResult,
    BlackboardUpdates,
    EditorResult,
)
from app import provider_service
from app.models import Blackboard, Course, EvidenceCard, ResearchBrief, Section

# json still used by generate_outline fallback

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent invocation helper
# ---------------------------------------------------------------------------


async def _invoke_agent(agent, message: str):
    """Invoke a langchain agent and return structured response or parsed output."""
    msg_preview = message[:120].replace("\n", " ")
    logger.info("[invoke_agent] Calling agent with message: %s...", msg_preview)
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]}
        )
    except AttributeError:
        logger.debug("[invoke_agent] ainvoke not available, falling back to sync invoke")
        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": [{"role": "user", "content": message}]},
        )
    if "structured_response" in result and result["structured_response"] is not None:
        resp_type = type(result["structured_response"]).__name__
        logger.info("[invoke_agent] Got structured_response of type %s", resp_type)
        return result["structured_response"]
    last_message = result["messages"][-1]
    content = last_message.content if hasattr(last_message, "content") else str(last_message)
    logger.debug("[invoke_agent] No structured_response, parsing last message (%d chars)", len(content))
    try:
        data = json.loads(content)
        logger.info("[invoke_agent] Parsed JSON from message content")
        return data
    except (json.JSONDecodeError, ValueError):
        logger.error("[invoke_agent] Failed to parse agent output: %s", content[:500])
        raise ValueError(f"Failed to parse agent output: {content[:500]}")


# ---------------------------------------------------------------------------
# Helper functions for the full pipeline
# ---------------------------------------------------------------------------


async def get_course(course_id, session: AsyncSession) -> Course:
    """Fetch a course with sections eager-loaded."""
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")
    return course


async def get_research_briefs(
    course_id, session: AsyncSession
) -> list[ResearchBrief]:
    """Fetch all research briefs for a course."""
    result = await session.execute(
        select(ResearchBrief).where(ResearchBrief.course_id == course_id)
    )
    return list(result.scalars().all())


async def update_course_status(
    course_id, status: str, session: AsyncSession
) -> None:
    """Update course.status and commit."""
    result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if course:
        course.status = status
        await session.commit()



def _generate_discovery_queries(topic: str, instructions: str | None) -> list[str]:
    """Generate 3-5 broad search queries from the topic for discovery research."""
    queries = [
        f"{topic} fundamentals overview",
        f"{topic} key concepts explained",
        f"{topic} learning roadmap beginner to advanced",
    ]
    # Add instruction-specific queries if provided
    if instructions:
        queries.append(f"{topic} {instructions}")
    # Add a best-practices / authoritative query
    queries.append(f"{topic} best practices authoritative guide")
    return queries[:5]  # Cap at 5 queries


async def discover_topic(
    topic: str,
    instructions: str | None = None,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
    on_event: EventCallback | None = None,
) -> TopicBrief:
    """Run discovery research on a topic using web search + synthesis agent.

    1. Generate broad search queries from the topic
    2. Search via configured search provider for each query
    3. Pass all results to discovery researcher agent for synthesis
    4. Return TopicBrief
    """
    from app import search_service

    credentials = credentials or {}

    # Generate search queries
    queries = _generate_discovery_queries(topic, instructions)
    logger.info("Discovery research: %d queries for topic '%s'", len(queries), topic)

    # Emit query events before launching searches
    if on_event:
        for i, query in enumerate(queries):
            await on_event("query", {"index": i, "total": len(queries), "query": query})

    # Run searches via configured provider (in parallel)
    all_search_results = []
    search_tasks = [
        search_service.search(
            search_provider, query, search_credentials or {},
            max_results=5, search_depth="basic",
        )
        for query in queries
    ]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for i, (query, result) in enumerate(zip(queries, search_results)):
        if isinstance(result, BaseException):
            logger.warning("[discover] Search failed for query '%s': %s", query, result)
            continue
        logger.debug("[discover] Query %d/%d returned %d results", i + 1, len(queries), len(result))
        for r in result:
            all_search_results.append({
                "title": r.title,
                "url": r.url,
                "content": r.content,
                "score": r.score,
            })
        # Emit per-result source events and query_done
        if on_event:
            for r in result:
                await on_event("source", {"query_index": i, "title": r.title, "url": r.url, "snippet": r.content[:200] if r.content else ""})
            await on_event("query_done", {"index": i, "result_count": len(result)})

    if not all_search_results:
        logger.error("[discover] All %d searches failed — no results to synthesize", len(queries))
        raise RuntimeError("All searches failed — no results to synthesize")

    # Emit discovery_done with total count
    if on_event:
        await on_event("discovery_done", {"total_sources": len(all_search_results)})

    logger.info("[discover] Collected %d search results from %d queries", len(all_search_results), len(queries))

    # Pass results to discovery researcher agent for synthesis
    message = (
        f"Topic: {topic}\n"
    )
    if instructions:
        message += f"Learner instructions: {instructions}\n"
    message += f"\nSearch results:\n{json.dumps(all_search_results, indent=2)}"

    # Emit synthesizing event before invoking synthesis agent
    if on_event:
        await on_event("synthesizing", {})

    logger.info("[discover] Invoking discovery researcher agent...")
    researcher = create_discovery_researcher(provider, model, credentials, extra_fields)
    result = await _invoke_agent(researcher, message)

    # Ensure we have a TopicBrief
    if isinstance(result, TopicBrief):
        logger.info("[discover] Discovery complete: %d key concepts, %d subtopics", len(result.key_concepts), len(result.subtopics))
        if on_event:
            await on_event("synthesis_done", {"key_concepts": result.key_concepts, "subtopics": result.subtopics})
        return result
    elif isinstance(result, dict):
        brief = TopicBrief(**result)
        logger.info("[discover] Discovery complete (from dict): %d key concepts, %d subtopics", len(brief.key_concepts), len(brief.subtopics))
        if on_event:
            await on_event("synthesis_done", {"key_concepts": brief.key_concepts, "subtopics": brief.subtopics})
        return brief
    else:
        raise ValueError(f"Discovery researcher returned unexpected type: {type(result)}")


async def generate_outline(
    topic: str,
    instructions: str | None = None,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
    on_event: EventCallback | None = None,
) -> tuple[CourseOutlineWithBriefs, bool]:
    """Invoke discovery research + planner to generate a grounded course outline.

    Returns (CourseOutlineWithBriefs, ungrounded_flag).
    If discovery fails, falls back to ungrounded planning (ungrounded=True).
    """
    from app import search_service

    credentials = credentials or {}

    # Step 1: Discovery research (wrapped in try/except for fallback)
    logger.info("[outline] Starting outline generation for topic='%s' (search_provider=%s)", topic, search_provider or "none")
    topic_brief = None
    ungrounded = False
    try:
        if search_service.is_configured(search_provider, search_credentials):
            topic_brief = await discover_topic(
                topic, instructions, provider, model, credentials, extra_fields,
                search_provider, search_credentials,
                on_event=on_event,
            )
            logger.info("[outline] Discovery research completed successfully")
        else:
            logger.warning("[outline] No search provider configured — skipping discovery, will be ungrounded")
            ungrounded = True
            if on_event:
                await on_event("ungrounded", {"reason": "no_search_provider"})
    except Exception as e:
        logger.warning("[outline] Discovery research failed, falling back to ungrounded planning: %s", e)
        ungrounded = True
        if on_event:
            await on_event("ungrounded", {"reason": str(e)})

    # Step 2: Plan with topic brief context
    if on_event:
        await on_event("planning", {})
    logger.info("[outline] Invoking planner agent (ungrounded=%s)...", ungrounded)
    message = f"Generate a course outline for the topic: {topic}"
    if instructions:
        message += f"\n\nLearner instructions: {instructions}"
    if topic_brief:
        message += f"\n\nResearch findings:\n{topic_brief.model_dump_json()}"

    planner = create_planner(provider, model, credentials, extra_fields)
    result = await _invoke_agent(planner, message)

    # Ensure we have a CourseOutlineWithBriefs
    if isinstance(result, CourseOutlineWithBriefs):
        return result, ungrounded
    elif isinstance(result, dict):
        return CourseOutlineWithBriefs(**result), ungrounded
    else:
        raise ValueError(f"Planner returned unexpected type: {type(result)}")


async def generate_lessons(
    course_id,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Legacy monolithic pipeline: research -> verify -> write -> edit per section.

    Superseded by pipeline.py for production use. Retained only for existing
    tests until they are migrated (Phase 5). The ``update_pipeline_status``
    calls are no-ops since the legacy status dict has been removed.
    """
    credentials = credentials or {}

    def update_pipeline_status(
        _course_id: str, _section: int | None, _stage: str
    ) -> None:
        """No-op — legacy pipeline status tracking removed in Phase 3."""
        pass
    pipeline_key = str(course_id)
    try:
        course = await get_course(course_id, session)
        briefs = await get_research_briefs(course_id, session)

        # 1. Parallel section research
        await update_course_status(course_id, "researching", session)
        update_pipeline_status(pipeline_key, None, "researching")
        await research_all_sections(course_id, briefs, session, provider, model, credentials, extra_fields, search_provider, search_credentials)

        # 2. Sequential per section: verify -> write -> edit
        blackboard = await create_blackboard(course_id, session)

        for section in sorted(course.sections, key=lambda s: s.position):
            cards = await get_evidence_cards(
                course_id, section.position, session
            )

            # Verify
            update_pipeline_status(
                pipeline_key, section.position, "verifying"
            )
            await update_course_status(course_id, "verifying", session)
            brief = next(
                (
                    b
                    for b in briefs
                    if b.section_position == section.position
                ),
                None,
            )
            if brief and cards:
                verification = await verify_evidence(cards, brief, session, provider, model, credentials, extra_fields)
                if verification.needs_more_research:
                    new_card_items = await research_section_targeted(
                        verification.gaps, provider, model, credentials, extra_fields,
                        search_provider, search_credentials,
                    )
                    if new_card_items:
                        await save_evidence_cards(
                            course_id,
                            section.position,
                            new_card_items,
                            session,
                        )
                    cards = await get_evidence_cards(
                        course_id, section.position, session
                    )
                    await verify_evidence(cards, brief, session, provider, model, credentials, extra_fields)

            # Write → Edit → Persist (wrapped per-section so one failure doesn't stop others)
            try:
                update_pipeline_status(
                    pipeline_key, section.position, "writing"
                )
                await update_course_status(course_id, "writing", session)
                draft = await write_section(
                    cards, blackboard, section, list(course.sections), session, provider, model, credentials, extra_fields
                )

                if not draft or not draft.strip():
                    logger.error(
                        "Writer returned empty content for section %s",
                        section.position,
                    )
                    update_pipeline_status(
                        pipeline_key, section.position, "failed"
                    )
                    continue

                # Edit
                update_pipeline_status(
                    pipeline_key, section.position, "editing"
                )
                await update_course_status(course_id, "editing", session)
                editor_result = await edit_section(
                    draft, blackboard, cards, section.position, session, provider, model, credentials, extra_fields
                )

                # Persist
                verified_cards = [c for c in cards if c.verified]
                final_content = editor_result.edited_content if editor_result.edited_content.strip() else draft
                citations = extract_citations(final_content, verified_cards)
                section.content = final_content
                section.citations = citations
                await session.commit()

                # Update blackboard (failure here should not crash the pipeline)
                try:
                    await update_blackboard(
                        blackboard, editor_result.blackboard_updates, session
                    )
                except Exception as e:
                    logger.warning(
                        "Blackboard update failed for section %s: %s",
                        section.position,
                        e,
                    )

                update_pipeline_status(
                    pipeline_key, section.position, "completed"
                )
            except Exception as e:
                logger.error(
                    "Write/edit failed for section %s: %s",
                    section.position,
                    e,
                )
                update_pipeline_status(
                    pipeline_key, section.position, "failed"
                )
                continue

        # Check if all sections have content
        result = await session.execute(
            select(Section).where(Section.course_id == course_id)
        )
        all_sections = result.scalars().all()
        all_have_content = all(s.content for s in all_sections)
        if not all_have_content:
            failed_positions = [s.position for s in all_sections if not s.content]
            logger.warning(
                "Course %s finished with missing content in sections: %s",
                course_id,
                failed_positions,
            )
        await update_course_status(course_id, "completed", session)

    except Exception as e:
        logger.error(
            "Pipeline failed for course %s: %s", course_id, e
        )
        try:
            await update_course_status(course_id, "failed", session)
        except Exception:
            pass
        update_pipeline_status(pipeline_key, None, "failed")


# ---------------------------------------------------------------------------
# Phase 3: Section researcher + evidence cards
# ---------------------------------------------------------------------------


async def research_section(
    brief: ResearchBrief,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> list[EvidenceCardItem]:
    """Research a single section by searching each must-answer question.

    1. For each question in the brief, search via configured provider
    2. Aggregate all search results
    3. Pass results + questions to the section researcher agent
    4. Return the extracted evidence cards
    """
    from app import search_service

    credentials = credentials or {}

    logger.info("[research] Section %s: researching %d questions", brief.section_position, len(brief.questions))
    all_results: list[dict] = []

    search_tasks = [
        search_service.search(
            search_provider, question, search_credentials or {},
            max_results=5, search_depth="basic",
        )
        for question in brief.questions
    ]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for i, (question, result) in enumerate(zip(brief.questions, search_results)):
        if isinstance(result, BaseException):
            logger.warning("[research] Section %s: search failed for question '%s': %s", brief.section_position, question[:60], result)
            continue
        logger.debug("[research] Section %s: question %d/%d returned %d results", brief.section_position, i + 1, len(brief.questions), len(result))
        for r in result:
            all_results.append({
                "title": r.title,
                "url": r.url,
                "content": r.content,
                "score": r.score,
            })

    if not all_results:
        logger.error("[research] Section %s: all %d searches failed", brief.section_position, len(brief.questions))
        raise RuntimeError(f"All searches failed for section {brief.section_position}")

    logger.info("[research] Section %s: collected %d search results, invoking researcher agent...", brief.section_position, len(all_results))

    # Pass to section researcher agent for evidence card extraction
    message = (
        f"Research brief:\n"
        f"Questions: {json.dumps(brief.questions)}\n\n"
        f"Search results:\n{json.dumps(all_results, indent=2)}"
    )

    researcher = create_section_researcher(provider, model, credentials, extra_fields)
    result = await _invoke_agent(researcher, message)

    # Ensure we have an EvidenceCardSet
    if isinstance(result, EvidenceCardSet):
        logger.info("[research] Section %s: extracted %d evidence cards", brief.section_position, len(result.cards))
        return result.cards
    elif isinstance(result, dict):
        card_set = EvidenceCardSet(**result)
        logger.info("[research] Section %s: extracted %d evidence cards (from dict)", brief.section_position, len(card_set.cards))
        return card_set.cards
    else:
        raise ValueError(f"Section researcher returned unexpected type: {type(result)}")


async def research_all_sections(
    course_id,
    briefs: list[ResearchBrief],
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Run section research in parallel for all section-level briefs.

    Filters to section-level briefs (section_position is not None),
    runs asyncio.gather with return_exceptions=True, and saves
    evidence cards for each successful result.
    """
    credentials = credentials or {}
    section_briefs = [b for b in briefs if b.section_position is not None]

    if not section_briefs:
        logger.warning("No section-level research briefs found for course %s", course_id)
        return

    logger.info(
        "Starting parallel research for %d sections (course %s)",
        len(section_briefs),
        course_id,
    )

    results = await asyncio.gather(
        *[research_section(brief, provider, model, credentials, extra_fields, search_provider, search_credentials) for brief in section_briefs],
        return_exceptions=True,
    )

    for brief, result in zip(section_briefs, results):
        if isinstance(result, Exception):
            logger.error(
                "Research failed for section %s (course %s): %s",
                brief.section_position,
                course_id,
                result,
            )
            continue
        await save_evidence_cards(
            course_id, brief.section_position, result, session
        )

    logger.info("Parallel section research complete for course %s", course_id)


async def save_evidence_cards(
    course_id,
    section_position: int,
    cards: list[EvidenceCardItem],
    session: AsyncSession,
) -> None:
    """Bulk insert EvidenceCard rows from a list of EvidenceCardItem."""
    db_cards = [
        EvidenceCard(
            course_id=course_id,
            section_position=section_position,
            claim=card.claim,
            source_url=card.source_url,
            source_title=card.source_title,
            source_tier=card.source_tier,
            passage=card.passage,
            retrieved_date=date.today(),
            confidence=card.confidence,
            caveat=card.caveat,
            explanation=card.explanation,
        )
        for card in cards
    ]
    session.add_all(db_cards)
    await session.commit()
    logger.info(
        "Saved %d evidence cards for section %s (course %s)",
        len(db_cards),
        section_position,
        course_id,
    )


async def get_evidence_cards(
    course_id, section_position: int, session: AsyncSession
) -> list[EvidenceCard]:
    """Query evidence cards for a specific section of a course."""
    result = await session.execute(
        select(EvidenceCard).where(
            EvidenceCard.course_id == course_id,
            EvidenceCard.section_position == section_position,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Phase 4: Verifier agent + re-research
# ---------------------------------------------------------------------------


def _format_cards_for_verifier(cards: list[EvidenceCard]) -> str:
    """Format evidence cards as a numbered list for the verifier agent.

    Each card is formatted with its claim, source, passage, confidence,
    and explanation so the verifier can assess quality.
    """
    lines = []
    for i, card in enumerate(cards):
        lines.append(
            f"[Card {i}]\n"
            f"  Claim: {card.claim}\n"
            f"  Source URL: {card.source_url}\n"
            f"  Source Title: {card.source_title}\n"
            f"  Source Tier: {card.source_tier}\n"
            f"  Passage: {card.passage}\n"
            f"  Confidence: {card.confidence}\n"
            f"  Caveat: {card.caveat or 'None'}\n"
            f"  Explanation: {card.explanation}"
        )
    return "\n\n".join(lines)


async def _update_card_verification(
    cards: list[EvidenceCard], session: AsyncSession
) -> None:
    """Persist updated verified/verification_note fields for cards in DB."""
    for card in cards:
        await session.merge(card)
    await session.commit()


async def verify_evidence(
    cards: list[EvidenceCard],
    brief: ResearchBrief,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> VerificationResult:
    """Invoke the verifier agent to check evidence quality for a section.

    1. Format cards and brief questions into a message
    2. Invoke verifier agent (no tools — pure LLM judgment)
    3. Update card verified status and verification_note in DB
    4. Return VerificationResult
    """
    credentials = credentials or {}
    logger.info("[verify] Verifying %d evidence cards against %d questions", len(cards), len(brief.questions))
    message = (
        f"Research brief questions:\n{json.dumps(brief.questions)}\n\n"
        f"Evidence cards:\n{_format_cards_for_verifier(cards)}"
    )

    verifier = create_verifier(provider, model, credentials, extra_fields)
    result = await _invoke_agent(verifier, message)

    # Ensure we have a VerificationResult
    if isinstance(result, dict):
        result = VerificationResult(**result)

    # Update card verified status in DB
    verified_count = 0
    for v in result.card_verifications:
        if 0 <= v.card_index < len(cards):
            cards[v.card_index].verified = v.verified
            cards[v.card_index].verification_note = v.note
            if v.verified:
                verified_count += 1

    await _update_card_verification(cards, session)

    logger.info(
        "[verify] Verification complete: %d/%d cards verified, needs_more_research=%s, gaps=%d",
        verified_count, len(result.card_verifications), result.needs_more_research, len(result.gaps),
    )
    if result.gaps:
        logger.debug("[verify] Gaps: %s", result.gaps)

    return result


async def research_section_targeted(
    gaps: list[str],
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> list[EvidenceCardItem]:
    """One retry with targeted queries for specific gaps.

    For each gap, search with search_depth="advanced" and max_results=3.
    Pass results to the section researcher agent for evidence card extraction.
    """
    from app import search_service

    credentials = credentials or {}

    all_results: list[dict] = []

    for gap in gaps:
        try:
            results = await search_service.search(
                search_provider, gap, search_credentials or {},
                max_results=3, search_depth="advanced",
            )
            for r in results:
                all_results.append({
                    "title": r.title,
                    "url": r.url,
                    "content": r.content,
                    "score": r.score,
                })
        except Exception as e:
            logger.warning(
                "Targeted search failed for gap '%s': %s", gap, e
            )
            continue

    if not all_results:
        logger.warning("All targeted searches failed — no new results")
        return []

    logger.info(
        "Targeted re-research: %d results from %d gaps",
        len(all_results),
        len(gaps),
    )

    # Reuse section researcher for evidence card extraction
    message = (
        f"Fill these specific gaps:\n{json.dumps(gaps)}\n\n"
        f"Search results:\n{json.dumps(all_results, indent=2)}"
    )

    researcher = create_section_researcher(provider, model, credentials, extra_fields)
    result = await _invoke_agent(researcher, message)

    # Ensure we have an EvidenceCardSet
    if isinstance(result, EvidenceCardSet):
        return result.cards
    elif isinstance(result, dict):
        card_set = EvidenceCardSet(**result)
        return card_set.cards
    else:
        raise ValueError(
            f"Section researcher returned unexpected type: {type(result)}"
        )


# ---------------------------------------------------------------------------
# Phase 5: Blackboard CRUD
# ---------------------------------------------------------------------------


async def create_blackboard(course_id, session: AsyncSession) -> Blackboard:
    """Create an empty Blackboard row for a course and return it."""
    bb = Blackboard(course_id=course_id)
    session.add(bb)
    await session.commit()
    await session.refresh(bb)
    logger.info("Created blackboard for course %s", course_id)
    return bb


async def get_blackboard(course_id, session: AsyncSession) -> Blackboard | None:
    """Query Blackboard by course_id. Returns None if not found."""
    result = await session.execute(
        select(Blackboard).where(Blackboard.course_id == course_id)
    )
    return result.scalar_one_or_none()


async def update_blackboard(
    blackboard: Blackboard, updates: BlackboardUpdates, session: AsyncSession
) -> None:
    """Merge BlackboardUpdates into existing Blackboard JSON fields.

    Uses dict.update() for glossary and concept_ownership so prior
    sections' data is preserved.  If validation fails, logs a warning
    and skips — never crashes the pipeline.
    """
    try:
        # Validate updates is the right type
        if isinstance(updates, dict):
            updates = BlackboardUpdates(**updates)

        # Merge glossary
        glossary = dict(blackboard.glossary or {})
        glossary.update(updates.new_glossary_terms or {})
        blackboard.glossary = glossary

        # Merge concept ownership
        ownership = dict(blackboard.concept_ownership or {})
        ownership.update(updates.new_concept_ownership or {})
        blackboard.concept_ownership = ownership

        # Merge coverage map — topics_covered keyed by a running list
        coverage = dict(blackboard.coverage_map or {})
        if updates.topics_covered:
            # Always copy the list to avoid SQLAlchemy JSON tracking issues
            existing_topics = list(coverage.get("all_topics", []))
            existing_topics.extend(updates.topics_covered)
            coverage["all_topics"] = existing_topics
        blackboard.coverage_map = coverage

        # Merge key points
        key_points = dict(blackboard.key_points or {})
        if updates.key_points_summary:
            # Store under a generic key; pipeline can pass section_position
            # but BlackboardUpdates doesn't carry it — just append
            count = len(key_points)
            key_points[str(count)] = updates.key_points_summary
        blackboard.key_points = key_points

        # Append new sources to source log
        source_log = list(blackboard.source_log or [])
        source_log.extend(updates.new_sources or [])
        blackboard.source_log = source_log

        await session.commit()
        logger.info("Blackboard updated for course %s", blackboard.course_id)

    except Exception as e:
        logger.warning(
            "Blackboard update failed (skipping, not crashing): %s", e
        )
        await session.rollback()


# ---------------------------------------------------------------------------
# Phase 5: Writer service (evidence-aware)
# ---------------------------------------------------------------------------


def _format_cards_for_writer(cards: list[EvidenceCard]) -> str:
    """Format evidence cards as a numbered list for the writer.

    Cards are 1-indexed to match the [N] citation markers the writer uses.
    """
    if not cards:
        return "(No evidence cards available for this section.)"
    lines = []
    for i, card in enumerate(cards, start=1):
        caveat_str = f"\n  Caveat: {card.caveat}" if card.caveat else ""
        lines.append(
            f"[{i}] {card.claim}\n"
            f"  Source: {card.source_title} ({card.source_url})\n"
            f"  Passage: {card.passage}\n"
            f"  Confidence: {card.confidence}\n"
            f"  Tier: {card.source_tier}"
            f"{caveat_str}"
        )
    return "\n\n".join(lines)


def _format_blackboard_for_agent(blackboard: Blackboard | None) -> str:
    """Format blackboard state as a human-readable string for writer/editor."""
    if blackboard is None:
        return "(Blackboard is empty — this is the first section.)"

    parts = []

    glossary = blackboard.glossary or {}
    if glossary:
        terms = []
        for term, info in glossary.items():
            if isinstance(info, dict):
                defn = info.get("definition", "")
                sec = info.get("defined_in_section", "?")
                terms.append(f"  - {term}: {defn} (defined in section {sec})")
            else:
                terms.append(f"  - {term}: {info}")
        parts.append("GLOSSARY:\n" + "\n".join(terms))
    else:
        parts.append("GLOSSARY: (empty)")

    ownership = blackboard.concept_ownership or {}
    if ownership:
        items = [f"  - {concept}: section {pos}" for concept, pos in ownership.items()]
        parts.append("CONCEPT OWNERSHIP:\n" + "\n".join(items))
    else:
        parts.append("CONCEPT OWNERSHIP: (empty)")

    coverage = blackboard.coverage_map or {}
    all_topics = coverage.get("all_topics", [])
    if all_topics:
        parts.append("TOPICS ALREADY COVERED:\n  - " + "\n  - ".join(all_topics))
    else:
        parts.append("TOPICS ALREADY COVERED: (none)")

    key_points = blackboard.key_points or {}
    if key_points:
        items = [f"  - {v}" for v in key_points.values()]
        parts.append("KEY POINTS FROM PRIOR SECTIONS:\n" + "\n".join(items))

    return "\n\n".join(parts)


def _format_outline_context(outline: Sequence) -> str:
    """Format the full outline as a numbered list for context."""
    lines = []
    for s in outline:
        if isinstance(s, dict):
            lines.append(f"{s['position']}. {s['title']} — {s['summary']}")
        else:
            # Section ORM object or similar
            lines.append(f"{s.position}. {s.title} — {s.summary}")
    return "\n".join(lines)


async def write_section(
    cards: list[EvidenceCard],
    blackboard: Blackboard | None,
    section,
    outline: Sequence,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> str:
    """Invoke the writer agent to generate a single section with evidence.

    1. Filter to verified cards only
    2. Build message with section info, outline context, evidence cards, blackboard
    3. Invoke writer (plain markdown output, NOT structured)
    4. Return draft markdown string
    """
    credentials = credentials or {}

    # Filter to verified cards only
    verified_cards = [c for c in cards if c.verified]

    # Build the section info
    if isinstance(section, dict):
        sec_title = section["title"]
        sec_summary = section["summary"]
    else:
        sec_title = section.title
        sec_summary = section.summary

    logger.info("[write] Writing section '%s' with %d verified cards (of %d total), blackboard=%s",
                sec_title, len(verified_cards), len(cards), "present" if blackboard else "empty")

    # Build the message
    outline_text = _format_outline_context(outline)
    cards_text = _format_cards_for_writer(verified_cards)
    blackboard_text = _format_blackboard_for_agent(blackboard)

    message = (
        f"Write the lesson content for this section:\n\n"
        f"Section title: {sec_title}\n"
        f"Section summary: {sec_summary}\n\n"
        f"--- FULL COURSE OUTLINE (for context) ---\n{outline_text}\n\n"
        f"--- VERIFIED EVIDENCE CARDS ---\n{cards_text}\n\n"
        f"--- BLACKBOARD (shared course knowledge) ---\n{blackboard_text}\n\n"
        f"Write the section now. Start with ## {sec_title}"
    )

    # Invoke LLM directly — the writer needs plain markdown, not structured
    # output via an agent framework. Direct call avoids tool distractions.
    from app.agent import WRITER_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    logger.info("[write] Invoking LLM for section '%s'...", sec_title)
    llm = provider_service.build_chat_model(provider, model, credentials, extra_fields)
    messages = [SystemMessage(content=WRITER_PROMPT), HumanMessage(content=message)]
    response = await llm.ainvoke(messages)
    content = response.content if hasattr(response, "content") else str(response)

    content_len = len(content.strip()) if content else 0
    if content_len > 0:
        logger.info("[write] Writer produced %d chars for section '%s'", content_len, sec_title)
    else:
        logger.warning("[write] Writer returned EMPTY content for section '%s'", sec_title)
    return content


# ---------------------------------------------------------------------------
# Phase 5: Editor service
# ---------------------------------------------------------------------------


async def edit_section(
    draft: str,
    blackboard: Blackboard | None,
    cards: list[EvidenceCard],
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> EditorResult:
    """Invoke the editor agent to polish a draft and generate blackboard updates.

    1. Build message with draft, blackboard state, evidence cards, section position
    2. Invoke editor agent (structured output via ToolStrategy)
    3. Return EditorResult
    """
    credentials = credentials or {}

    logger.info("[edit] Editing section %d (draft=%d chars, cards=%d, blackboard=%s)",
                section_position, len(draft), len(cards), "present" if blackboard else "empty")

    cards_text = _format_cards_for_writer(cards)
    blackboard_text = _format_blackboard_for_agent(blackboard)

    message = (
        f"Edit the following draft for section {section_position}.\n\n"
        f"--- DRAFT ---\n{draft}\n\n"
        f"--- BLACKBOARD (shared course knowledge) ---\n{blackboard_text}\n\n"
        f"--- EVIDENCE CARDS ---\n{cards_text}\n\n"
        f"Section position: {section_position}\n\n"
        f"Polish the draft, check citations, and generate blackboard updates."
    )

    logger.info("[edit] Invoking editor agent for section %d...", section_position)
    editor = create_editor(provider, model, credentials, extra_fields)
    result = await _invoke_agent(editor, message)

    # Ensure we have an EditorResult
    if isinstance(result, EditorResult):
        edited_len = len(result.edited_content.strip()) if result.edited_content else 0
        logger.info("[edit] Section %d: editor returned %d chars, %d glossary terms, %d topics covered",
                    section_position, edited_len,
                    len(result.blackboard_updates.new_glossary_terms),
                    len(result.blackboard_updates.topics_covered))
        if edited_len == 0:
            logger.warning("[edit] Section %d: editor returned EMPTY edited_content", section_position)
        return result
    elif isinstance(result, dict):
        er = EditorResult(**result)
        edited_len = len(er.edited_content.strip()) if er.edited_content else 0
        logger.info("[edit] Section %d: editor returned %d chars (from dict)", section_position, edited_len)
        if edited_len == 0:
            logger.warning("[edit] Section %d: editor returned EMPTY edited_content", section_position)
        return er
    else:
        raise ValueError(f"Editor returned unexpected type: {type(result)}")


# ---------------------------------------------------------------------------
# Phase 5: Citation extraction
# ---------------------------------------------------------------------------


def extract_citations(
    content: str, cards: list[EvidenceCard]
) -> list[dict]:
    """Map [N] markers in content to evidence card source info.

    Returns a list of citation dicts: {number, claim, source_url, source_title}.
    Cards are 1-indexed. Out-of-range markers are silently skipped.
    """
    citation_numbers = set(int(n) for n in re.findall(r"\[(\d+)\]", content))
    citations = []
    for n in sorted(citation_numbers):
        if 1 <= n <= len(cards):
            card = cards[n - 1]  # 1-indexed
            citations.append({
                "number": n,
                "claim": card.claim,
                "source_url": card.source_url,
                "source_title": card.source_title,
            })
    return citations


# ---------------------------------------------------------------------------
# Standalone per-stage functions for internal API endpoints
# ---------------------------------------------------------------------------
# Each function is self-contained: reads from DB, does LLM/search work,
# writes results to DB, and returns a serializable dict.
# These are called by the internal router endpoints (Phase 2, Milestone 3).
# ---------------------------------------------------------------------------


async def run_discover_and_plan(
    course_id,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
    *,
    skip_status_update: bool = False,
) -> dict:
    """Run discovery research + planning for a course.

    Reads the course from DB, runs discovery research via search provider,
    runs the planner agent, creates sections + research briefs in DB,
    and returns them.

    Returns:
        {
            "sections": [{position, title, summary, id}, ...],
            "research_briefs": [{id, section_position, questions, source_policy}, ...],
            "ungrounded": bool,
        }
    """
    from sqlalchemy.orm import selectinload

    credentials = credentials or {}

    # Fetch course
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")

    # Run discovery research + planner
    outline_with_briefs, ungrounded = await generate_outline(
        course.topic, course.instructions, provider, model, credentials, extra_fields,
        search_provider, search_credentials,
    )

    # Set ungrounded flag
    course.ungrounded = ungrounded

    # Delete existing sections (in case of re-run)
    for section in list(course.sections):
        await session.delete(section)
    await session.flush()

    # Delete existing research briefs
    old_briefs_result = await session.execute(
        select(ResearchBrief).where(ResearchBrief.course_id == course_id)
    )
    for brief in old_briefs_result.scalars().all():
        await session.delete(brief)
    await session.flush()

    # Create section rows
    new_sections = []
    for section_data in outline_with_briefs.sections:
        section = Section(
            course_id=course.id,
            position=section_data.position,
            title=section_data.title,
            summary=section_data.summary,
        )
        session.add(section)
        new_sections.append(section)

    # Save discovery brief if research succeeded
    if not ungrounded:
        discovery_brief = ResearchBrief(
            course_id=course.id,
            section_position=None,
            questions=[],
            source_policy={},
            findings="Discovery research completed successfully",
        )
        session.add(discovery_brief)

    # Save per-section research briefs
    new_briefs = []
    for brief_item in outline_with_briefs.research_briefs:
        research_brief = ResearchBrief(
            course_id=course.id,
            section_position=brief_item.section_position,
            questions=brief_item.questions,
            source_policy=brief_item.source_policy,
        )
        session.add(research_brief)
        new_briefs.append(research_brief)

    if not skip_status_update:
        course.status = "outline_ready"
    await session.commit()

    # Refresh to get IDs
    for s in new_sections:
        await session.refresh(s)
    for b in new_briefs:
        await session.refresh(b)

    return {
        "sections": [
            {
                "id": str(s.id),
                "position": s.position,
                "title": s.title,
                "summary": s.summary,
            }
            for s in new_sections
        ],
        "research_briefs": [
            {
                "id": str(b.id),
                "section_position": b.section_position,
                "questions": b.questions,
                "source_policy": b.source_policy,
            }
            for b in new_briefs
        ],
        "ungrounded": ungrounded,
    }


async def run_research_section(
    course_id,
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> dict:
    """Run section researcher for one section.

    Reads the research brief from DB, runs the researcher agent
    (search + evidence card extraction), saves evidence cards
    to DB, and returns them.

    Returns:
        {
            "evidence_cards": [{id, claim, source_url, ...}, ...],
        }
    """
    credentials = credentials or {}

    # Fetch research brief for this section
    result = await session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course_id,
            ResearchBrief.section_position == section_position,
        )
    )
    brief = result.scalar_one_or_none()
    if brief is None:
        raise ValueError(
            f"No research brief found for course {course_id}, "
            f"section {section_position}"
        )

    # Run section researcher (search + agent)
    card_items = await research_section(brief, provider, model, credentials, extra_fields, search_provider, search_credentials)

    # Save evidence cards to DB
    await save_evidence_cards(course_id, section_position, card_items, session)

    # Fetch saved cards to return with IDs
    saved_cards = await get_evidence_cards(course_id, section_position, session)

    return {
        "evidence_cards": [
            {
                "id": str(card.id),
                "section_position": card.section_position,
                "claim": card.claim,
                "source_url": card.source_url,
                "source_title": card.source_title,
                "source_tier": card.source_tier,
                "passage": card.passage,
                "retrieved_date": str(card.retrieved_date),
                "confidence": card.confidence,
                "caveat": card.caveat,
                "explanation": card.explanation,
                "verified": card.verified,
            }
            for card in saved_cards
        ],
    }


async def run_verify_section(
    course_id,
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> dict:
    """Run verifier for one section.

    Reads evidence cards and research brief from DB, runs the verifier
    agent, updates verification status in DB, and optionally runs
    targeted re-research if needed.

    Returns:
        {
            "verification_result": {
                "cards_verified": int,
                "cards_total": int,
                "needs_more_research": bool,
                "gaps": [str, ...],
            },
        }
    """
    credentials = credentials or {}

    # Fetch evidence cards
    cards = await get_evidence_cards(course_id, section_position, session)
    if not cards:
        raise ValueError(
            f"No evidence cards found for course {course_id}, "
            f"section {section_position}"
        )

    # Fetch research brief
    result = await session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course_id,
            ResearchBrief.section_position == section_position,
        )
    )
    brief = result.scalar_one_or_none()
    if brief is None:
        raise ValueError(
            f"No research brief found for course {course_id}, "
            f"section {section_position}"
        )

    # Run verifier
    verification = await verify_evidence(cards, brief, session, provider, model, credentials, extra_fields)

    # If verifier says we need more research, do targeted re-research
    if verification.needs_more_research:
        new_card_items = await research_section_targeted(
            verification.gaps, provider, model, credentials, extra_fields,
            search_provider, search_credentials,
        )
        if new_card_items:
            await save_evidence_cards(
                course_id, section_position, new_card_items, session
            )
        # Re-fetch and re-verify
        cards = await get_evidence_cards(course_id, section_position, session)
        verification = await verify_evidence(cards, brief, session, provider, model, credentials, extra_fields)

    verified_count = sum(
        1 for v in verification.card_verifications if v.verified
    )

    return {
        "verification_result": {
            "cards_verified": verified_count,
            "cards_total": len(verification.card_verifications),
            "needs_more_research": verification.needs_more_research,
            "gaps": verification.gaps,
        },
    }


async def run_write_section(
    course_id,
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """Run writer for one section.

    Reads evidence cards, blackboard, and section info from DB,
    runs the writer agent, saves content + citations to the section,
    and returns them.

    Returns:
        {
            "content": str,
            "citations": [{number, claim, source_url, source_title}, ...],
        }
    """
    from sqlalchemy.orm import selectinload

    credentials = credentials or {}

    # Fetch course with sections for outline context
    course_result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")

    # Find the target section
    section = next(
        (s for s in course.sections if s.position == section_position),
        None,
    )
    if section is None:
        raise ValueError(
            f"Section {section_position} not found in course {course_id}"
        )

    # Fetch evidence cards
    cards = await get_evidence_cards(course_id, section_position, session)

    # Fetch or create blackboard
    blackboard = await get_blackboard(course_id, session)
    if blackboard is None:
        blackboard = await create_blackboard(course_id, session)

    # Run writer with retry on empty output
    max_write_attempts = 3
    draft = ""
    for attempt in range(1, max_write_attempts + 1):
        draft = await write_section(
            cards, blackboard, section, list(course.sections), session, provider, model, credentials, extra_fields
        )
        if draft and draft.strip():
            break
        logger.warning(
            "Writer returned empty content for section %s (attempt %d/%d)",
            section_position, attempt, max_write_attempts,
        )
    if not draft or not draft.strip():
        raise RuntimeError(
            f"Writer returned empty content for section {section_position} "
            f"after {max_write_attempts} attempts"
        )

    # Extract citations from draft
    verified_cards = [c for c in cards if c.verified]
    citations = extract_citations(draft, verified_cards)

    # Persist content + citations to the section
    section.content = draft
    section.citations = citations
    await session.commit()

    return {
        "content": draft,
        "citations": citations,
    }


async def run_edit_section(
    course_id,
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """Run editor for one section.

    Reads draft content, blackboard, and evidence cards from DB,
    runs the editor agent, updates content + blackboard, and returns
    the result.

    Returns:
        {
            "edited_content": str,
            "blackboard_updates": {
                "new_glossary_terms": dict,
                "new_concept_ownership": dict,
                "topics_covered": [str, ...],
                "key_points_summary": str,
                "new_sources": [dict, ...],
            },
        }
    """
    from sqlalchemy.orm import selectinload

    credentials = credentials or {}

    # Fetch course with sections
    course_result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if course is None:
        raise ValueError(f"Course {course_id} not found")

    # Find the target section
    section = next(
        (s for s in course.sections if s.position == section_position),
        None,
    )
    if section is None:
        raise ValueError(
            f"Section {section_position} not found in course {course_id}"
        )

    if not section.content:
        raise ValueError(
            f"Section {section_position} has no draft content to edit"
        )

    # Fetch evidence cards
    cards = await get_evidence_cards(course_id, section_position, session)

    # Fetch blackboard
    blackboard = await get_blackboard(course_id, session)

    # Run editor with retry on empty output, fall back to draft
    draft = section.content
    max_edit_attempts = 3
    edited_content = ""
    editor_result = None
    for attempt in range(1, max_edit_attempts + 1):
        editor_result = await edit_section(
            draft, blackboard, cards, section_position, session, provider, model, credentials, extra_fields
        )
        if editor_result.edited_content and editor_result.edited_content.strip():
            edited_content = editor_result.edited_content
            break
        logger.warning(
            "Editor returned empty content for section %s (attempt %d/%d)",
            section_position, attempt, max_edit_attempts,
        )

    # Fall back to draft if editor consistently returns empty
    if not edited_content or not edited_content.strip():
        logger.warning(
            "Editor returned empty content for section %s after %d attempts, "
            "falling back to writer draft",
            section_position, max_edit_attempts,
        )
        edited_content = draft

    # Update section content with edited version
    verified_cards = [c for c in cards if c.verified]
    citations = extract_citations(edited_content, verified_cards)
    section.content = edited_content
    section.citations = citations
    await session.commit()

    # Update blackboard
    if blackboard and editor_result:
        try:
            await update_blackboard(
                blackboard, editor_result.blackboard_updates, session
            )
        except Exception as e:
            logger.warning(
                "Blackboard update failed for section %s: %s",
                section_position,
                e,
            )

    return {
        "edited_content": edited_content,
        "blackboard_updates": {
            "new_glossary_terms": editor_result.blackboard_updates.new_glossary_terms,
            "new_concept_ownership": editor_result.blackboard_updates.new_concept_ownership,
            "topics_covered": editor_result.blackboard_updates.topics_covered,
            "key_points_summary": editor_result.blackboard_updates.key_points_summary,
            "new_sources": editor_result.blackboard_updates.new_sources,
        },
    }
