import asyncio
import json
import logging
import re
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import (
    create_planner,
    create_writer,
    create_editor,
    create_discovery_researcher,
    create_section_researcher,
    create_verifier,
    CourseOutline,
    CourseOutlineWithBriefs,
    CourseContent,
    SectionContent,
    TopicBrief,
    EvidenceCardItem,
    EvidenceCardSet,
    VerificationResult,
    BlackboardUpdates,
    EditorResult,
)
from app.config import settings
from app.models import Blackboard, Course, EvidenceCard, ResearchBrief, Section

# json still used by generate_outline fallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline status tracking (in-memory, not persisted to DB)
# ---------------------------------------------------------------------------

_pipeline_status: dict[str, dict] = {}


def update_pipeline_status(
    course_id: str, section: int | None, stage: str
) -> None:
    """Update the in-memory pipeline status for a course."""
    if course_id not in _pipeline_status:
        _pipeline_status[course_id] = {
            "stage": stage,
            "current_section": section,
            "sections": {},
        }
    _pipeline_status[course_id]["stage"] = stage
    _pipeline_status[course_id]["current_section"] = section
    if section is not None:
        _pipeline_status[course_id]["sections"][section] = stage


def get_pipeline_status(course_id: str) -> dict | None:
    """Return current pipeline status for a course, or None."""
    return _pipeline_status.get(course_id)


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


async def _invoke_agent(agent, message: str):
    """Invoke a Deep Agents agent with async/sync fallback.

    Returns the structured_response if available, otherwise parses
    the last message content as JSON.
    """
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]}
        )
    except AttributeError:
        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": [{"role": "user", "content": message}]},
        )

    # Preferred path: ToolStrategy places output in structured_response
    if "structured_response" in result and result["structured_response"] is not None:
        return result["structured_response"]

    # Fallback: try to parse the last assistant message as JSON
    last_message = result["messages"][-1]
    content = last_message.content if hasattr(last_message, "content") else str(last_message)

    try:
        data = json.loads(content)
        return data
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse agent output: %s", content[:500])
        raise ValueError(
            f"Failed to parse agent output: {content[:500]}"
        )


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


async def discover_topic(topic: str, instructions: str | None = None) -> TopicBrief:
    """Run discovery research on a topic using Tavily + synthesis agent.

    1. Generate broad search queries from the topic
    2. Call AsyncTavilyClient for each query (max_results=5, search_depth="basic")
    3. Pass all results to discovery researcher agent for synthesis
    4. Return TopicBrief
    """
    from tavily import AsyncTavilyClient

    # Generate search queries
    queries = _generate_discovery_queries(topic, instructions)
    logger.info("Discovery research: %d queries for topic '%s'", len(queries), topic)

    # Run Tavily searches
    client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
    all_search_results = []
    for query in queries:
        try:
            response = await client.search(
                query=query,
                search_depth="basic",
                max_results=5,
            )
            # Extract structured results (title, url, content, score)
            for r in response.get("results", []):
                all_search_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                })
        except Exception as e:
            logger.warning("Tavily search failed for query '%s': %s", query, e)
            continue

    if not all_search_results:
        raise RuntimeError("All Tavily searches failed — no results to synthesize")

    logger.info("Discovery research: %d total search results collected", len(all_search_results))

    # Pass results to discovery researcher agent for synthesis
    researcher = create_discovery_researcher()
    message = (
        f"Topic: {topic}\n"
    )
    if instructions:
        message += f"Learner instructions: {instructions}\n"
    message += f"\nSearch results:\n{json.dumps(all_search_results, indent=2)}"

    result = await _invoke_agent(researcher, message)

    # Ensure we have a TopicBrief
    if isinstance(result, TopicBrief):
        return result
    elif isinstance(result, dict):
        return TopicBrief(**result)
    else:
        raise ValueError(f"Discovery researcher returned unexpected type: {type(result)}")


async def generate_outline(
    topic: str, instructions: str | None = None
) -> tuple[CourseOutlineWithBriefs, bool]:
    """Invoke discovery research + planner to generate a grounded course outline.

    Returns (CourseOutlineWithBriefs, ungrounded_flag).
    If discovery fails, falls back to ungrounded planning (ungrounded=True).
    """
    # Step 1: Discovery research (wrapped in try/except for fallback)
    topic_brief = None
    ungrounded = False
    try:
        if settings.TAVILY_API_KEY:
            topic_brief = await discover_topic(topic, instructions)
            logger.info("Discovery research completed successfully")
        else:
            logger.warning("TAVILY_API_KEY not set — skipping discovery research")
            ungrounded = True
    except Exception as e:
        logger.warning("Discovery research failed, falling back to ungrounded planning: %s", e)
        ungrounded = True

    # Step 2: Plan with topic brief context
    planner = create_planner()

    message = f"Generate a course outline for the topic: {topic}"
    if instructions:
        message += f"\n\nLearner instructions: {instructions}"
    if topic_brief:
        message += f"\n\nResearch findings:\n{topic_brief.model_dump_json()}"

    result = await _invoke_agent(planner, message)

    # Ensure we have a CourseOutlineWithBriefs
    if isinstance(result, CourseOutlineWithBriefs):
        return result, ungrounded
    elif isinstance(result, dict):
        return CourseOutlineWithBriefs(**result), ungrounded
    else:
        raise ValueError(f"Planner returned unexpected type: {type(result)}")


def _split_markdown_sections(markdown: str, expected_count: int) -> list[SectionContent]:
    """Split writer markdown output by ## headings into per-section content."""
    import re

    # Split on ## headings
    parts = re.split(r"^##\s+", markdown, flags=re.MULTILINE)

    # parts[0] is any text before the first ## (usually empty), skip it
    section_texts = [p.strip() for p in parts[1:] if p.strip()]

    sections = []
    for i, text in enumerate(section_texts):
        # Re-add the ## heading
        sections.append(SectionContent(
            position=i + 1,
            content=f"## {text}",
        ))

    return sections


async def generate_lessons(course_id, session: AsyncSession) -> None:
    """Full M2 pipeline: research -> verify -> write -> edit for each section.

    Called as a background task. Updates course status and pipeline status
    at each stage.  ``course_id`` may be a UUID or string – DB queries
    receive it as-is, while the in-memory pipeline status dict always uses
    ``str(course_id)``.
    """
    pipeline_key = str(course_id)
    try:
        course = await get_course(course_id, session)
        briefs = await get_research_briefs(course_id, session)

        # 1. Parallel section research
        await update_course_status(course_id, "researching", session)
        update_pipeline_status(pipeline_key, None, "researching")
        await research_all_sections(course_id, briefs, session)

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
                verification = await verify_evidence(cards, brief, session)
                if verification.needs_more_research:
                    new_card_items = await research_section_targeted(
                        verification.gaps
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
                    await verify_evidence(cards, brief, session)

            # Write → Edit → Persist (wrapped per-section so one failure doesn't stop others)
            try:
                update_pipeline_status(
                    pipeline_key, section.position, "writing"
                )
                await update_course_status(course_id, "writing", session)
                draft = await write_section(
                    cards, blackboard, section, list(course.sections), session
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
                    draft, blackboard, cards, section.position, session
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


async def research_section(brief: ResearchBrief) -> list[EvidenceCardItem]:
    """Research a single section by searching each must-answer question via Tavily.

    1. For each question in the brief, call AsyncTavilyClient.search
    2. Aggregate all search results
    3. Pass results + questions to the section researcher agent
    4. Return the extracted evidence cards
    """
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
    all_results: list[dict] = []

    for question in brief.questions:
        try:
            response = await client.search(
                query=question,
                search_depth="basic",
                max_results=5,
            )
            for r in response.get("results", []):
                all_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                })
        except Exception as e:
            logger.warning(
                "Tavily search failed for question '%s' (section %s): %s",
                question,
                brief.section_position,
                e,
            )
            continue

    if not all_results:
        raise RuntimeError(
            f"All Tavily searches failed for section {brief.section_position}"
        )

    logger.info(
        "Section %s: collected %d search results from %d questions",
        brief.section_position,
        len(all_results),
        len(brief.questions),
    )

    # Pass to section researcher agent for evidence card extraction
    researcher = create_section_researcher()
    message = (
        f"Research brief:\n"
        f"Questions: {json.dumps(brief.questions)}\n\n"
        f"Search results:\n{json.dumps(all_results, indent=2)}"
    )

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


async def research_all_sections(
    course_id, briefs: list[ResearchBrief], session: AsyncSession
) -> None:
    """Run section research in parallel for all section-level briefs.

    Filters to section-level briefs (section_position is not None),
    runs asyncio.gather with return_exceptions=True, and saves
    evidence cards for each successful result.
    """
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
        *[research_section(brief) for brief in section_briefs],
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
) -> VerificationResult:
    """Invoke the verifier agent to check evidence quality for a section.

    1. Format cards and brief questions into a message
    2. Invoke verifier agent (no tools — pure LLM judgment)
    3. Update card verified status and verification_note in DB
    4. Return VerificationResult
    """
    verifier = create_verifier()
    message = (
        f"Research brief questions:\n{json.dumps(brief.questions)}\n\n"
        f"Evidence cards:\n{_format_cards_for_verifier(cards)}"
    )

    result = await _invoke_agent(verifier, message)

    # Ensure we have a VerificationResult
    if isinstance(result, dict):
        result = VerificationResult(**result)

    # Update card verified status in DB
    for v in result.card_verifications:
        if 0 <= v.card_index < len(cards):
            cards[v.card_index].verified = v.verified
            cards[v.card_index].verification_note = v.note

    await _update_card_verification(cards, session)

    logger.info(
        "Verification complete: %d/%d cards verified, needs_more_research=%s",
        sum(1 for v in result.card_verifications if v.verified),
        len(result.card_verifications),
        result.needs_more_research,
    )

    return result


async def research_section_targeted(gaps: list[str]) -> list[EvidenceCardItem]:
    """One retry with targeted queries for specific gaps.

    For each gap, call AsyncTavilyClient.search with search_depth="advanced"
    (2 credits per query) and max_results=3. Pass results to the section
    researcher agent for evidence card extraction.
    """
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
    all_results: list[dict] = []

    for gap in gaps:
        try:
            response = await client.search(
                query=gap,
                search_depth="advanced",
                max_results=3,
            )
            for r in response.get("results", []):
                all_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
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
    researcher = create_section_researcher()
    message = (
        f"Fill these specific gaps:\n{json.dumps(gaps)}\n\n"
        f"Search results:\n{json.dumps(all_results, indent=2)}"
    )

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
) -> str:
    """Invoke the writer agent to generate a single section with evidence.

    1. Filter to verified cards only
    2. Build message with section info, outline context, evidence cards, blackboard
    3. Invoke writer (plain markdown output, NOT structured)
    4. Return draft markdown string
    """
    writer = create_writer()

    # Filter to verified cards only
    verified_cards = [c for c in cards if c.verified]

    # Build the section info
    if isinstance(section, dict):
        sec_title = section["title"]
        sec_summary = section["summary"]
    else:
        sec_title = section.title
        sec_summary = section.summary

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

    # Invoke writer — returns plain markdown, NOT structured output
    try:
        result = await writer.ainvoke(
            {"messages": [{"role": "user", "content": message}]}
        )
    except AttributeError:
        result = await asyncio.to_thread(
            writer.invoke,
            {"messages": [{"role": "user", "content": message}]},
        )

    # Extract markdown from the last message
    last_message = result["messages"][-1]
    content = (
        last_message.content
        if hasattr(last_message, "content")
        else str(last_message)
    )

    logger.info("Writer produced draft for section '%s'", sec_title)
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
) -> EditorResult:
    """Invoke the editor agent to polish a draft and generate blackboard updates.

    1. Build message with draft, blackboard state, evidence cards, section position
    2. Invoke editor (structured output via ToolStrategy)
    3. Return EditorResult
    """
    editor = create_editor()

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

    result = await _invoke_agent(editor, message)

    # Ensure we have an EditorResult
    if isinstance(result, EditorResult):
        return result
    elif isinstance(result, dict):
        return EditorResult(**result)
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
    course_id, session: AsyncSession
) -> dict:
    """Run discovery research + planning for a course.

    Reads the course from DB, runs discovery research via Tavily,
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
        course.topic, course.instructions
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
    course_id, section_position: int, session: AsyncSession
) -> dict:
    """Run section researcher for one section.

    Reads the research brief from DB, runs the researcher agent
    (Tavily search + evidence card extraction), saves evidence cards
    to DB, and returns them.

    Returns:
        {
            "evidence_cards": [{id, claim, source_url, ...}, ...],
        }
    """
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

    # Run section researcher (Tavily search + agent)
    card_items = await research_section(brief)

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
    course_id, section_position: int, session: AsyncSession
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
    verification = await verify_evidence(cards, brief, session)

    # If verifier says we need more research, do targeted re-research
    if verification.needs_more_research:
        new_card_items = await research_section_targeted(verification.gaps)
        if new_card_items:
            await save_evidence_cards(
                course_id, section_position, new_card_items, session
            )
        # Re-fetch and re-verify
        cards = await get_evidence_cards(course_id, section_position, session)
        verification = await verify_evidence(cards, brief, session)

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
    course_id, section_position: int, session: AsyncSession
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

    # Run writer
    draft = await write_section(
        cards, blackboard, section, list(course.sections), session
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
    course_id, section_position: int, session: AsyncSession
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

    # Run editor
    editor_result = await edit_section(
        section.content, blackboard, cards, section_position, session
    )

    # Update section content with edited version
    verified_cards = [c for c in cards if c.verified]
    citations = extract_citations(editor_result.edited_content, verified_cards)
    section.content = editor_result.edited_content
    section.citations = citations
    await session.commit()

    # Update blackboard
    if blackboard:
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
        "edited_content": editor_result.edited_content,
        "blackboard_updates": {
            "new_glossary_terms": editor_result.blackboard_updates.new_glossary_terms,
            "new_concept_ownership": editor_result.blackboard_updates.new_concept_ownership,
            "topics_covered": editor_result.blackboard_updates.topics_covered,
            "key_points_summary": editor_result.blackboard_updates.key_points_summary,
            "new_sources": editor_result.blackboard_updates.new_sources,
        },
    }
