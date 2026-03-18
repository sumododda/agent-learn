import json
import logging
from typing import Sequence

from app.agent import create_planner, create_writer, CourseOutline, CourseContent

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


async def generate_lessons(
    topic: str,
    instructions: str | None,
    sections: Sequence[dict],
) -> CourseContent:
    """Invoke the writer agent to generate lesson content for all sections.

    Uses the standalone writer (with response_format) so that the result
    is returned as a typed CourseContent in result["structured_response"].

    Parameters
    ----------
    topic : str
        The course topic.
    instructions : str | None
        Optional learner instructions.
    sections : Sequence[dict]
        List of dicts with keys ``position``, ``title``, ``summary`` — the
        full outline the writer needs for coherence.
    """
    writer = create_writer()

    # Build the outline context so the writer sees the full structure
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
        f"in order from section 1 to section {len(sections)}."
    )

    # ainvoke with fallback — same pattern as generate_outline
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

    # Preferred path: ToolStrategy places output in structured_response
    if "structured_response" in result and result["structured_response"] is not None:
        return result["structured_response"]

    # Fallback: try to parse the last assistant message as JSON
    last_message = result["messages"][-1]
    content = last_message.content if hasattr(last_message, "content") else str(last_message)

    try:
        data = json.loads(content)
        return CourseContent(**data)
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse writer output: %s", content[:500])
        raise ValueError(
            f"Failed to parse writer output as CourseContent: {content[:500]}"
        )
