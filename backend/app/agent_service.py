import asyncio
import json
import logging
from typing import Sequence

from app.agent import (
    create_planner,
    create_writer,
    create_discovery_researcher,
    CourseOutline,
    CourseOutlineWithBriefs,
    CourseContent,
    SectionContent,
    TopicBrief,
)
from app.config import settings

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
