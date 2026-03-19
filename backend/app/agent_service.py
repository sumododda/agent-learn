import asyncio
import json
import logging
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import (
    create_planner,
    create_writer,
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
)
from app.config import settings
from app.models import EvidenceCard, ResearchBrief

# json still used by generate_outline fallback

logger = logging.getLogger(__name__)


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


async def generate_lessons(
    topic: str,
    instructions: str | None,
    sections: Sequence[dict],
) -> CourseContent:
    """Invoke the writer agent to generate markdown lesson content.

    The writer returns plain markdown with ## headings per section.
    We split by headings and map to CourseContent.
    """
    writer = create_writer()

    outline_text = "\n".join(
        f"{s['position']}. {s['title']} — {s['summary']}" for s in sections
    )

    message = (
        f"Generate lesson content for the following course.\n\n"
        f"Topic: {topic}\n"
    )
    if instructions:
        message += f"Learner instructions: {instructions}\n"
    message += (
        f"\nFull course outline:\n{outline_text}\n\n"
        f"Write detailed markdown lesson content for ALL {len(sections)} sections, "
        f"in order from section 1 to section {len(sections)}. "
        f"Start each section with ## followed by the section title."
    )

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
    content = last_message.content if hasattr(last_message, "content") else str(last_message)

    parsed = _split_markdown_sections(content, len(sections))

    if not parsed:
        raise ValueError(f"Writer returned no parseable sections. Output: {content[:500]}")

    logger.info("Writer produced %d sections (expected %d)", len(parsed), len(sections))
    return CourseContent(sections=parsed)


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
