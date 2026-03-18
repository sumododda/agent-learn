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


# --- Extended planner schemas (M2: research briefs) ---


class ResearchBriefItem(BaseModel):
    section_position: int
    questions: list[str]
    source_policy: dict  # {"preferred_tiers": [1, 2], "scope": "...", "out_of_scope": "..."}


class CourseOutlineWithBriefs(BaseModel):
    sections: list[OutlineSection]
    research_briefs: list[ResearchBriefItem]


# --- Structured output schemas for discovery researcher ---


class TopicBrief(BaseModel):
    key_concepts: list[str]
    subtopics: list[str]
    authoritative_sources: list[str]
    learning_progression: str
    open_debates: list[str]
    raw_search_results: list[dict]  # preserve for reference


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

PLANNER_PROMPT = """You are a course planner. Given a topic and optional learner instructions, generate a structured course outline with research briefs.

Your job:
- Identify the key concepts the learner needs to understand
- Order sections by conceptual dependency (prerequisites first)
- Write a concise summary for each section describing what the lesson will cover
- Target 5-10 sections depending on topic scope
- For each section, generate a research brief to guide evidence gathering

Each section needs:
- position: integer starting at 1
- title: clear, descriptive section title
- summary: 1-2 sentences describing what this section covers

Each research brief needs:
- section_position: integer matching the section position
- questions: 3-5 must-answer questions that the section's lesson must address with evidence
- source_policy: a dict with keys:
  - "preferred_tiers": list of preferred source tiers [1, 2] where 1=official docs/papers, 2=reputable blogs/tutorials, 3=forums/repos
  - "scope": brief description of what sources should cover
  - "out_of_scope": topics or sources to avoid for this section

If you receive research findings about the topic, use them to inform your outline structure and research briefs. The findings contain key concepts, subtopics, authoritative sources, and learning progressions discovered from web research. Incorporate this knowledge to create a well-grounded outline.

Output your response as a structured CourseOutlineWithBriefs with both a list of sections and a list of research_briefs.
Do NOT include introductions or conclusions as separate sections unless they contain real content.
Focus on substance — every section should teach something specific.
Every section MUST have a corresponding research brief."""

DISCOVERY_RESEARCHER_PROMPT = """You are a topic discovery researcher. Your job is to synthesize web search results into a structured topic brief that will guide course planning.

You will receive:
- A topic description
- Raw search results from web searches (title, URL, content snippets)

Your task:
1. Identify the key concepts that any learner must understand about this topic
2. Discover the natural subtopics and their relationships
3. Identify authoritative sources (official documentation, academic papers, well-known tutorials)
4. Determine the best learning progression (what should be taught first, what builds on what)
5. Note any open debates, controversies, or areas where expert opinions differ
6. Preserve the raw search results for reference

Guidelines:
- Focus on factual, well-supported information from the search results
- Prefer information from multiple corroborating sources
- Note when information comes from a single source or may be outdated
- Identify both foundational concepts and advanced topics
- Consider different skill levels when suggesting learning progression
- Be specific about authoritative sources — include names and URLs when available

Output a structured TopicBrief with all fields populated based on the search results."""


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
    guaranteed CourseOutlineWithBriefs in result["structured_response"].
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=PLANNER_PROMPT,
        response_format=ToolStrategy(CourseOutlineWithBriefs),
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


def create_discovery_researcher():
    """Create a discovery researcher agent that synthesizes search results.

    The agent receives pre-fetched Tavily search results and produces
    a structured TopicBrief. It does NOT call Tavily itself — the
    service layer handles all search API calls.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=DISCOVERY_RESEARCHER_PROMPT,
        response_format=ToolStrategy(TopicBrief),
        tools=[],
        name="agent-learn-discovery-researcher",
    )
    return agent
