import json
import logging
from typing import Sequence

from app.agent import create_planner, create_writer, CourseOutline, CourseContent, SectionContent
# json still used by generate_outline fallback

logger = logging.getLogger(__name__)


async def generate_outline(topic: str, instructions: str | None = None) -> CourseOutline:
    """Invoke the planner agent to generate a structured course outline.

    Uses the standalone planner (with response_format) so that the result
    is returned as a typed CourseOutline in result["structured_response"].
    """
    planner = create_planner()

    message = f"Generate a course outline for the topic: {topic}"
    if instructions:
        message += f"\n\nLearner instructions: {instructions}"

    # Deep Agents returns a LangGraph state dict.
    # Use ainvoke for async compatibility with FastAPI.
    try:
        result = await planner.ainvoke(
            {"messages": [{"role": "user", "content": message}]}
        )
    except AttributeError:
        # Fallback if ainvoke is not available on the compiled graph
        import asyncio

        result = await asyncio.to_thread(
            planner.invoke,
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
        return CourseOutline(**data)
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse planner output: %s", content[:500])
        raise ValueError(
            f"Failed to parse planner output as CourseOutline: {content[:500]}"
        )


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
        import asyncio
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
