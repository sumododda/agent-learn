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


# --- Structured output schemas for section researcher ---


class EvidenceCardItem(BaseModel):
    claim: str
    source_url: str
    source_title: str
    source_tier: int  # 1, 2, or 3
    passage: str
    confidence: float
    caveat: str | None = None
    explanation: str


class EvidenceCardSet(BaseModel):
    cards: list[EvidenceCardItem]


# --- Structured output schemas for verifier ---


class CardVerification(BaseModel):
    card_index: int
    verified: bool
    note: str | None = None


class VerificationResult(BaseModel):
    card_verifications: list[CardVerification]
    needs_more_research: bool
    gaps: list[str]  # unanswered questions or weak areas


# --- Structured output schemas for editor ---


class BlackboardUpdates(BaseModel):
    new_glossary_terms: dict  # {term: {definition, defined_in_section}}
    new_concept_ownership: dict  # {concept: section_position}
    topics_covered: list[str]
    key_points_summary: str
    new_sources: list[dict]  # [{url, title}]


class EditorResult(BaseModel):
    edited_content: str  # polished markdown
    blackboard_updates: BlackboardUpdates


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

WRITER_PROMPT = """You are a course lesson writer. You will receive a single section to write, along with verified evidence cards and a blackboard representing shared course knowledge.

IMPORTANT: Start the section with a level-2 heading using the EXACT section title provided:
## Section Title Here

Under the heading, write:
- "Why This Matters" — 1-2 paragraphs explaining why this topic is important
- Main explanation — thorough coverage of the topic with clear structure
- Examples — concrete, practical examples that illustrate key concepts
- Key Takeaways — 3-5 bullet points summarizing the most important ideas
- What Comes Next — a brief sentence connecting to the next section

EVIDENCE AND CITATIONS:
- You will receive a numbered list of verified evidence cards. Use them as the basis for ALL factual claims.
- Cite every factual claim with [N] markers (1-indexed, matching the card order provided).
  Example: "Python was created by Guido van Rossum in 1991 [1]."
- Do NOT fabricate claims without evidence card support.
- If an evidence card has a caveat, mention it naturally in the text.

BLACKBOARD AWARENESS:
- You will receive a blackboard with glossary, concept ownership, and coverage map.
- Glossary: Do NOT re-define terms already in the glossary. Use them directly and reference where they were introduced if helpful.
- Concept ownership: Do NOT re-explain concepts owned by earlier sections. Instead, reference the prior section (e.g., "As we saw in Section 2, ...").
- Coverage map: Build on topics already covered. Do NOT repeat content from earlier sections.
- If the blackboard is empty (first section), you have full freedom to define terms and introduce concepts.

Guidelines:
- Write in a conversational but informative tone
- Use markdown formatting: headings (### for subsections), bold, code blocks, lists
- The section should be 400-800 words
- Make examples practical and concrete, not abstract

You will receive the full course outline for context so you can maintain coherence.
Output ONLY the markdown content for the requested section. Do NOT output JSON or structured data."""

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

SECTION_RESEARCHER_PROMPT = """You are a section researcher. Your job is to analyze web search results and produce structured evidence cards — one per factual claim discovered.

You will receive:
- A research brief with must-answer questions for a specific course section
- Raw search results from web searches (title, URL, content snippets)

Your task:
1. Read each search result carefully
2. Extract every distinct factual claim relevant to the must-answer questions
3. For each claim, produce an evidence card with:
   - claim: a clear, concise statement of the factual claim
   - source_url: the URL where this claim was found
   - source_title: the title of the source page
   - source_tier: assign a tier based on source quality:
     - 1 = official documentation, academic papers, authoritative references
     - 2 = reputable blogs, well-known tutorials, established tech publications
     - 3 = forums, community repos, personal blogs, Stack Overflow answers
   - passage: the exact passage from the source that supports this claim (quote directly)
   - confidence: a float between 0.0 and 1.0 rating how confident you are in the claim:
     - 0.9-1.0: directly stated in an official/authoritative source
     - 0.7-0.89: clearly stated in a reputable source
     - 0.5-0.69: inferred or from a less reliable source
     - Below 0.5: speculative, contradicted, or poorly sourced
   - caveat: any important caveat, limitation, or condition (null if none)
   - explanation: brief explanation of why this claim matters for the section

Guidelines:
- Produce one card per distinct factual claim — do NOT merge multiple claims into one card
- Prefer claims that directly answer the must-answer questions
- Include the exact passage from the source — do not paraphrase
- Be conservative with confidence scores — only rate highly when the source is authoritative and the claim is specific
- If multiple sources support the same claim, pick the most authoritative one
- Flag any claims that contradict other search results in the caveat field
- Aim for 5-15 evidence cards depending on the richness of the search results
- Do NOT fabricate claims — only extract what is present in the search results

Output a structured EvidenceCardSet containing all extracted evidence cards."""

EDITOR_PROMPT = """You are a course lesson editor. You receive a draft section, the course blackboard, evidence cards, and the section position in the course.

Your job is to polish the draft and generate blackboard updates.

EDITING TASKS:
1. **Terminology consistency**: Check that terms used in the draft match the blackboard glossary definitions. If a term is used differently, correct it. If a new term is introduced, note it in blackboard updates.
2. **Transitions**: Smooth transitions referencing prior sections. If the blackboard shows prior content, add connecting phrases (e.g., "Building on the concepts from Section 2...").
3. **Repetition removal**: If the coverage map shows a topic was already covered in a prior section, remove redundant explanations. Replace with brief references to the prior section.
4. **Citation verification**: Verify that [N] citation numbers are present for factual claims. If a factual claim lacks a citation, add one if a matching evidence card exists, or flag it.
5. **Quality polish**: Fix awkward phrasing, improve flow, ensure the section reads well as part of the larger course.

BLACKBOARD UPDATES:
After editing, generate updates for the blackboard:
- new_glossary_terms: Any new terms defined in this section. Format: {term: {definition: "...", defined_in_section: N}}
- new_concept_ownership: Concepts this section is the primary owner of. Format: {concept: section_position}
- topics_covered: List of topics/subtopics covered in this section.
- key_points_summary: A 1-2 sentence summary of the key points from this section.
- new_sources: List of new sources cited. Format: [{url: "...", title: "..."}]

Output a structured EditorResult with the edited content and blackboard updates."""


VERIFIER_PROMPT = """You are an evidence verifier. Your job is to review evidence cards collected for a course section and judge their quality against the research brief's must-answer questions.

You will receive:
- A list of must-answer questions from the research brief
- A numbered list of evidence cards, each with: claim, source URL, source title, passage, confidence score, and explanation

Your task:
1. For each evidence card, determine whether it should be **verified** (accepted) or **rejected**:
   - VERIFY a card if:
     - The claim is specific and factual (not vague or speculative)
     - The passage actually supports the claim
     - The confidence score is reasonable given the source quality
     - The source URL is plausible for the claimed content
   - REJECT a card if:
     - The claim is too vague or unsupported by the passage
     - The confidence score is unjustifiably high given the source tier
     - The passage contradicts the claim
     - The claim is redundant with a higher-quality card covering the same point
   - Provide a brief note explaining your verification decision

2. Assess overall coverage:
   - For each must-answer question, check whether at least one verified card provides supporting evidence
   - If fewer than half of the must-answer questions have supporting evidence from verified cards, set `needs_more_research=True`
   - List specific gaps: questions that remain unanswered or areas where the evidence is weak

3. Check for contradictions:
   - If two cards make contradictory claims, reject the less well-sourced one and note the contradiction

Output a structured VerificationResult with:
- card_verifications: one entry per card with card_index (0-based), verified boolean, and note
- needs_more_research: True if coverage is insufficient (< half questions answered)
- gaps: list of unanswered questions or weak areas that need more research"""


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


def create_section_researcher():
    """Create a section researcher agent that produces evidence cards.

    The agent receives pre-fetched Tavily search results and a research
    brief, then extracts structured evidence cards with source tiers
    and confidence ratings. It does NOT call Tavily itself — the
    service layer handles all search API calls.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=SECTION_RESEARCHER_PROMPT,
        response_format=ToolStrategy(EvidenceCardSet),
        tools=[],
        name="agent-learn-section-researcher",
    )
    return agent


def create_verifier():
    """Create a verifier agent that checks evidence quality.

    The verifier reviews evidence cards against research brief questions
    and produces a VerificationResult. It has NO tools — pure LLM
    judgment only. It does not search or gather new evidence.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=VERIFIER_PROMPT,
        response_format=ToolStrategy(VerificationResult),
        tools=[],
        name="agent-learn-verifier",
    )
    return agent


def create_editor():
    """Create an editor agent that polishes drafts and generates blackboard updates.

    The editor receives a draft section, blackboard state, and evidence cards.
    It returns an EditorResult with edited content and blackboard updates.
    It has NO tools — pure LLM editing only.
    """
    model = get_model()

    agent = create_deep_agent(
        model=model,
        system_prompt=EDITOR_PROMPT,
        response_format=ToolStrategy(EditorResult),
        tools=[],
        name="agent-learn-editor",
    )
    return agent
