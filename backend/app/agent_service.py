import json
import logging

from app.agent import create_planner, CourseOutline

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
