from pydantic import BaseModel
from langchain.chat_models import init_chat_model
from langchain.agents.structured_output import ToolStrategy
from deepagents import create_deep_agent
from app.config import settings


def get_model():
    """Create an LLM instance via OpenRouter."""
    return init_chat_model(
        model=settings.OPENROUTER_MODEL,
        model_provider="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.OPENROUTER_API_KEY,
    )


# --- Structured output schemas for planner ---


class OutlineSection(BaseModel):
    position: int
    title: str
    summary: str


class CourseOutline(BaseModel):
    sections: list[OutlineSection]


# --- System prompts ---

SUPERVISOR_PROMPT = """You are the agent-learn supervisor. You coordinate course generation by delegating to specialized subagents.

When asked to generate a course outline, delegate to the planner subagent using the task() tool.
When asked to generate lesson content, delegate to the writer subagent using the task() tool.

Always delegate — do not generate course content yourself."""

PLANNER_PROMPT = """You are a course planner. Given a topic and optional learner instructions, generate a structured course outline.

Your job:
- Identify the key concepts the learner needs to understand
- Order sections by conceptual dependency (prerequisites first)
- Write a concise summary for each section describing what the lesson will cover
- Target 5-10 sections depending on topic scope

Each section needs:
- position: integer starting at 1
- title: clear, descriptive section title
- summary: 1-2 sentences describing what this section covers

Output your response as a structured CourseOutline with a list of sections.
Do NOT include introductions or conclusions as separate sections unless they contain real content.
Focus on substance — every section should teach something specific."""


# --- Planner subagent config (dict form, used by supervisor) ---

planner_subagent = {
    "name": "planner",
    "description": (
        "Generates a structured course outline from a topic and optional "
        "learner instructions. Use this when asked to create a course outline."
    ),
    "system_prompt": PLANNER_PROMPT,
    "tools": [],
}


def create_supervisor():
    """Create the agent-learn supervisor with subagents.

    The supervisor delegates to specialized subagents via the built-in
    task() tool.  In Phase 3 only the planner subagent is wired; the
    writer will be added in Phase 4.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=SUPERVISOR_PROMPT,
        subagents=[planner_subagent],
        name="agent-learn-supervisor",
    )
    return agent


def create_planner():
    """Create a standalone planner agent with structured output.

    This is the primary entry point for outline generation.  We invoke
    the planner directly (rather than through the supervisor) because
    Deep Agents excludes `structured_response` from subagent return
    state, so a supervisor-delegated call would lose the typed output.
    Calling the planner directly with response_format gives us a
    guaranteed CourseOutline in result["structured_response"].
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=PLANNER_PROMPT,
        response_format=ToolStrategy(CourseOutline),
        tools=[],
        name="agent-learn-planner",
    )
    return agent
