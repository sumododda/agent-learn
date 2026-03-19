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


# --- Structured output schemas for writer ---


class SectionContent(BaseModel):
    position: int
    content: str  # markdown


class CourseContent(BaseModel):
    sections: list[SectionContent]


# --- System prompts ---

SUPERVISOR_PROMPT = """You are the agent-learn supervisor. You coordinate course generation by delegating to specialized subagents.

When asked to generate a course outline, delegate to the planner subagent using the task() tool.
When asked to generate lesson content, delegate to the writer subagent using the task() tool.

Always delegate — do not generate course content yourself."""

WRITER_PROMPT = """You are a course lesson writer. Given a course outline with section titles and summaries, generate detailed markdown lesson content for each section.

IMPORTANT: Start each section with a level-2 heading using the EXACT section title from the outline:
## Section Title Here

Under each heading, write:
- "Why This Matters" — 1-2 paragraphs explaining why this topic is important
- Main explanation — thorough coverage of the topic with clear structure
- Examples — concrete, practical examples that illustrate key concepts
- Key Takeaways — 3-5 bullet points summarizing the most important ideas
- What Comes Next — a brief sentence connecting to the next section

Guidelines:
- Write in a conversational but informative tone
- Use markdown formatting: headings (### for subsections), bold, code blocks, lists
- Each section should be 400-800 words
- Build on concepts from earlier sections — maintain coherence
- Do not include citations (source grounding comes in a later milestone)
- Make examples practical and concrete, not abstract

You will receive the full course outline so you can maintain coherence across sections.
Generate content for ALL sections in order. Each section MUST start with ## followed by the section title."""

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


# --- Writer subagent config (dict form, used by supervisor) ---

writer_subagent = {
    "name": "writer",
    "description": (
        "Generates markdown lesson content for each section of an approved "
        "course outline. Use this when asked to generate lesson content."
    ),
    "system_prompt": WRITER_PROMPT,
    "tools": [],
}


def create_supervisor():
    """Create the agent-learn supervisor with subagents.

    The supervisor delegates to specialized subagents via the built-in
    task() tool.  It has access to both the planner (outline generation)
    and the writer (lesson content generation).
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=SUPERVISOR_PROMPT,
        subagents=[planner_subagent, writer_subagent],
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


def create_writer():
    """Create a standalone writer agent that returns plain markdown.

    No structured output — the writer returns prose which we split
    by ## headings in agent_service.py.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=WRITER_PROMPT,
        tools=[],
        name="agent-learn-writer",
    )
    return agent
