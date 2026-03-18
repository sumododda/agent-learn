# Milestone 2 — Implementation Plan

**Design spec:** `docs/superpowers/specs/2026-03-18-milestone-2-design.md`
**Created:** 2026-03-18

---

## Phase 0: Documentation Discovery (Reference)

Findings from doc discovery subagents. All implementation phases reference these patterns.

### Allowed APIs — Tavily Search

| API | Import | Notes |
|---|---|---|
| `AsyncTavilyClient` | `from tavily import AsyncTavilyClient` | Async client using `httpx.AsyncClient` internally |
| `client.search()` | `await client.search(query, ...)` | Returns dict with `results` list |
| Response fields | `result["title"]`, `["url"]`, `["content"]`, `["score"]` | `content` is extracted snippet; `raw_content` if `include_raw_content=True` |
| Search depth | `search_depth="basic"` (1 credit) or `"advanced"` (2 credits) | Advanced enables `chunks_per_source` |
| Max results | `max_results=5` (default) | Range: 0-20 |
| Domain filtering | `include_domains=[]`, `exclude_domains=[]` | Up to 300/150 domains |
| Error types | `MissingAPIKeyError`, `InvalidAPIKeyError`, `UsageLimitExceededError`, `BadRequestError`, `TimeoutError` | All from `tavily` package |
| Package | `pip install tavily-python` | MIT license, v0.7.23 |

**Copy-ready pattern — Async search:**
```python
from tavily import AsyncTavilyClient

client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
response = await client.search(
    query="machine learning fundamentals",
    search_depth="basic",
    max_results=5,
)
for r in response["results"]:
    # r["title"], r["url"], r["content"], r["score"]
```

**Anti-patterns:**
- Do NOT use `TavilyClient` (sync) — use `AsyncTavilyClient` to match the async FastAPI/agent pipeline
- Do NOT use `langchain-tavily` — use `tavily-python` directly since our agents use `tools=[]` and invoke Tavily programmatically in the service layer, not as a bound LLM tool
- Do NOT forget to set `TAVILY_API_KEY` environment variable

### Allowed APIs — Deep Agents (unchanged from M1 + new tool binding)

| API | Import | Notes |
|---|---|---|
| `create_deep_agent()` | `from deepagents import create_deep_agent` | Returns `CompiledStateGraph` |
| `init_chat_model()` | `from langchain.chat_models import init_chat_model` | OpenRouter: `model_provider="openai"`, `base_url="https://openrouter.ai/api/v1"` |
| `ToolStrategy` | `from langchain.agents.structured_output import ToolStrategy` | Structured output: `response_format=ToolStrategy(MyModel)`, result in `["structured_response"]` |
| Tool binding | `tools=[my_callable]` or `tools=[BaseTool]` | `create_deep_agent` accepts `Sequence[BaseTool \| Callable \| dict]` |

**Existing agent invocation pattern (copy from M1):**
```python
try:
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": message}]}
    )
except AttributeError:
    result = await asyncio.to_thread(
        agent.invoke,
        {"messages": [{"role": "user", "content": message}]},
    )
```

**Structured output extraction (copy from M1):**
```python
if "structured_response" in result and result["structured_response"] is not None:
    return result["structured_response"]
# fallback: parse result["messages"][-1].content as JSON
```

### Allowed APIs — Database Patterns (unchanged from M1)

| Pattern | Example | Notes |
|---|---|---|
| UUID PK | `id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)` | |
| Text column | `claim: Mapped[str] = mapped_column(Text, nullable=False)` | Always use `Text`, not `String` |
| Nullable | `caveat: Mapped[str \| None] = mapped_column(Text, nullable=True)` | Union syntax |
| JSON column | `glossary: Mapped[dict] = mapped_column(JSON, default=dict)` | For flexible structured data |
| FK | `course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"))` | |
| Timestamps | `created_at: Mapped[datetime] = mapped_column(server_default=func.now())` | `func.now()` for DB compat |
| Relationship | `cards: Mapped[list["EvidenceCard"]] = relationship(back_populates="course")` | Bidirectional |
| Response schema | `model_config = {"from_attributes": True}` | For ORM → Pydantic |
| Migration | `alembic revision --autogenerate -m "description"` then `alembic upgrade head` | |

### Allowed APIs — Frontend (unchanged from M1)

| API | Import/Path | Notes |
|---|---|---|
| Dynamic params | `params: Promise<{ id: string }>` | Must `await params` |
| `useRouter` | `from 'next/navigation'` | NOT `'next/router'` |
| Markdown | `react-markdown` + `prose` class | Already installed |
| Polling | `setInterval` + `useState` | For pipeline status updates |

---

## Phase 1: Database Schema + Migration

**Goal:** New tables (`research_briefs`, `evidence_cards`, `blackboard`) exist. Existing models extended (`Course.ungrounded`, new status values, `Section.citations`). Migration runs cleanly.

### Tasks

1. **New ORM models** (add to `backend/app/models.py`):

   **ResearchBrief:**
   ```python
   class ResearchBrief(Base):
       __tablename__ = "research_briefs"
       id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
       course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), nullable=False)
       section_position: Mapped[int | None] = mapped_column(nullable=True)  # null = discovery brief
       questions: Mapped[list] = mapped_column(JSON, default=list)
       source_policy: Mapped[dict] = mapped_column(JSON, default=dict)
       findings: Mapped[str | None] = mapped_column(Text, nullable=True)
       created_at: Mapped[datetime] = mapped_column(server_default=func.now())
   ```

   **EvidenceCard:**
   ```python
   class EvidenceCard(Base):
       __tablename__ = "evidence_cards"
       id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
       course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), nullable=False)
       section_position: Mapped[int] = mapped_column(nullable=False)
       claim: Mapped[str] = mapped_column(Text, nullable=False)
       source_url: Mapped[str] = mapped_column(Text, nullable=False)
       source_title: Mapped[str] = mapped_column(Text, nullable=False)
       source_tier: Mapped[int] = mapped_column(nullable=False)  # 1, 2, or 3
       passage: Mapped[str] = mapped_column(Text, nullable=False)
       retrieved_date: Mapped[date] = mapped_column(nullable=False)
       confidence: Mapped[float] = mapped_column(nullable=False)
       caveat: Mapped[str | None] = mapped_column(Text, nullable=True)
       explanation: Mapped[str] = mapped_column(Text, nullable=False)
       verified: Mapped[bool] = mapped_column(default=False)
       verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
       created_at: Mapped[datetime] = mapped_column(server_default=func.now())
   ```

   **Blackboard:**
   ```python
   class Blackboard(Base):
       __tablename__ = "blackboard"
       id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
       course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), unique=True, nullable=False)
       glossary: Mapped[dict] = mapped_column(JSON, default=dict)
       concept_ownership: Mapped[dict] = mapped_column(JSON, default=dict)
       coverage_map: Mapped[dict] = mapped_column(JSON, default=dict)
       key_points: Mapped[dict] = mapped_column(JSON, default=dict)
       source_log: Mapped[list] = mapped_column(JSON, default=list)
       open_questions: Mapped[list] = mapped_column(JSON, default=list)
       updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=datetime.now)
   ```

2. **Extend existing models:**
   - `Course`: Add `ungrounded: Mapped[bool] = mapped_column(default=False)`
   - `Section`: Add `citations: Mapped[list | None] = mapped_column(JSON, nullable=True)`
   - Add relationships: `Course.research_briefs`, `Course.evidence_cards`, `Course.blackboard`

3. **New Pydantic schemas** (add to `backend/app/schemas.py`):
   - `EvidenceCardResponse` — all evidence card fields, `model_config = {"from_attributes": True}`
   - `ResearchBriefResponse` — brief fields with `from_attributes`
   - `BlackboardResponse` — all blackboard fields with `from_attributes`
   - `PipelineStatus` / `SectionPipelineStatus` — for progress tracking
   - `Citation` — `number: int`, `claim: str`, `source_url: str`, `source_title: str`

4. **Alembic migration:**
   - `alembic revision --autogenerate -m "add research_briefs evidence_cards blackboard tables"`
   - `alembic upgrade head`

### Documentation references
- JSON columns: `Mapped[dict] = mapped_column(JSON, default=dict)` — Phase 0 DB patterns
- Unique constraint: `unique=True` on `course_id` in blackboard
- `func.now()` for server_default — matches M1 pattern

### Verification checklist
- [ ] `alembic upgrade head` creates all three new tables + modifies courses and sections
- [ ] New tables visible in DB with correct column types
- [ ] `Course.ungrounded` defaults to `False`
- [ ] `Section.citations` is nullable JSON
- [ ] Blackboard enforces one-per-course via unique constraint
- [ ] Existing tests still pass (no regressions)

### Anti-pattern guards
- Do NOT use `String` type — use `Text` for all text columns (matches M1)
- Do NOT forget `server_default=func.now()` on timestamps (not Python-side default)
- Do NOT use an enum column for `source_tier` — use int (1/2/3) for simplicity, validate in application layer

---

## Phase 2: Discovery Researcher + Extended Planner

**Goal:** `POST /api/courses` now runs discovery research (Tavily) before planning. Planner receives topic brief and outputs research briefs alongside the outline.

### Tasks

1. **Install Tavily:**
   - Add `tavily-python` to `backend/requirements.txt`
   - Add `TAVILY_API_KEY: str = ""` to `backend/app/config.py` Settings class

2. **Discovery researcher agent** (add to `backend/app/agent.py`):
   - Define `DISCOVERY_RESEARCHER_PROMPT` — instructs the agent to:
     - Given a topic, generate 3-5 broad search queries
     - Search each query via Tavily
     - Synthesize findings into a topic brief: key concepts, subtopics, authoritative sources, learning progressions, open debates
   - Define Pydantic schema for structured output:
     ```python
     class TopicBrief(BaseModel):
         key_concepts: list[str]
         subtopics: list[str]
         authoritative_sources: list[str]
         learning_progression: str
         open_debates: list[str]
         raw_search_results: list[dict]  # preserve for reference
     ```
   - Create `create_discovery_researcher()`:
     ```python
     def create_discovery_researcher():
         model = get_model()
         agent = create_deep_agent(
             model=model,
             system_prompt=DISCOVERY_RESEARCHER_PROMPT,
             response_format=ToolStrategy(TopicBrief),
             tools=[],
             name="agent-learn-discovery-researcher",
         )
         return agent
     ```
   - **Note on Tavily usage:** The discovery researcher does NOT use Tavily as an LLM tool. Instead, the service layer calls Tavily directly and passes results to the agent for synthesis. This avoids tool-binding complexity and gives us full control over search queries and error handling.

3. **Extended planner schemas** (add to `backend/app/agent.py`):
   ```python
   class ResearchBriefItem(BaseModel):
       section_position: int
       questions: list[str]
       source_policy: dict  # {"preferred_tiers": [1, 2], "scope": "...", "out_of_scope": "..."}

   class CourseOutlineWithBriefs(BaseModel):
       sections: list[OutlineSection]
       research_briefs: list[ResearchBriefItem]
   ```
   - Update `create_planner()` to use `ToolStrategy(CourseOutlineWithBriefs)`
   - Update `PLANNER_PROMPT` to include instructions for generating research briefs:
     - For each section, generate 3-5 must-answer questions
     - Specify source policy (preferred tiers, scope, out-of-scope topics)

4. **Discovery research service function** (add to `backend/app/agent_service.py`):
   ```python
   async def discover_topic(topic: str, instructions: str | None) -> TopicBrief:
       # 1. Generate broad search queries from topic
       queries = _generate_discovery_queries(topic, instructions)
       # 2. Run Tavily searches
       client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
       search_results = []
       for query in queries:
           result = await client.search(query, max_results=5, search_depth="basic")
           search_results.extend(result["results"])
       # 3. Pass results to discovery researcher agent for synthesis
       researcher = create_discovery_researcher()
       message = f"Topic: {topic}\n\nSearch results:\n{json.dumps(search_results, indent=2)}"
       result = await _invoke_agent(researcher, message)
       return result  # TopicBrief
   ```

5. **Extended generate_outline** (modify in `backend/app/agent_service.py`):
   ```python
   async def generate_outline(topic: str, instructions: str | None) -> CourseOutlineWithBriefs:
       # 1. Discovery research
       try:
           topic_brief = await discover_topic(topic, instructions)
           ungrounded = False
       except Exception:
           topic_brief = None
           ungrounded = True
       # 2. Plan with topic brief
       planner = create_planner()
       brief_context = f"\n\nResearch findings:\n{topic_brief.model_dump_json()}" if topic_brief else ""
       message = f"Generate a course outline for: {topic}. Instructions: {instructions or 'None'}{brief_context}"
       result = await _invoke_agent(planner, message)
       return result, ungrounded
   ```

6. **Update course creation endpoint** (modify `backend/app/routers/courses.py`):
   - `POST /api/courses` now:
     1. Creates course with `status="researching"`
     2. Calls `discover_topic()` → saves discovery brief to `research_briefs` table
     3. Calls planner with topic brief → saves outline + per-section research briefs
     4. Sets `course.ungrounded = True` if discovery failed
     5. Sets `status="outline_ready"`
     6. Returns `CourseResponse`

### Documentation references
- `AsyncTavilyClient`: Phase 0 Tavily section — async search pattern
- `create_deep_agent` with structured output: Phase 0 Deep Agents section
- Agent invocation: Phase 0 `ainvoke` / `to_thread` fallback pattern
- Course creation endpoint: existing pattern in `routers/courses.py`

### Verification checklist
- [ ] `tavily-python` installed and importable
- [ ] `TAVILY_API_KEY` loaded from environment
- [ ] Discovery researcher returns a `TopicBrief` with populated fields
- [ ] Planner returns `CourseOutlineWithBriefs` with sections AND research briefs
- [ ] `POST /api/courses` with a topic triggers Tavily searches and returns a grounded outline
- [ ] Discovery brief saved to `research_briefs` with `section_position=null`
- [ ] Per-section research briefs saved with correct `section_position` values
- [ ] When Tavily is unavailable, planner still works (falls back to model knowledge, sets `ungrounded=True`)

### Anti-pattern guards
- Do NOT bind Tavily as an LLM tool — call it directly in the service layer for control
- Do NOT block on Tavily failures — fall back to ungrounded planning
- Do NOT forget to save both the discovery brief AND per-section briefs to the DB
- Do NOT pass raw HTML/markdown from Tavily to the agent — pass structured `title/url/content` dicts

---

## Phase 3: Section Researcher + Evidence Cards

**Goal:** After outline approval, all sections are researched in parallel via Tavily. Evidence cards are saved to the database.

### Tasks

1. **Section researcher agent** (add to `backend/app/agent.py`):
   - Define `SECTION_RESEARCHER_PROMPT` — instructs the agent to:
     - Given a research brief (must-answer questions) and Tavily search results
     - Produce evidence cards: one card per factual claim discovered
     - Assign source tiers (1=official docs/papers, 2=reputable blogs, 3=forums/repos)
     - Rate confidence 0-1 based on source quality and claim specificity
     - Include exact passages from sources
   - Define structured output schema:
     ```python
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
     ```
   - Create `create_section_researcher()` with `ToolStrategy(EvidenceCardSet)`

2. **Section research service function** (add to `backend/app/agent_service.py`):
   ```python
   async def research_section(brief: ResearchBrief) -> list[EvidenceCardItem]:
       client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
       all_results = []
       for question in brief.questions:
           result = await client.search(question, max_results=5, search_depth="basic")
           all_results.extend(result["results"])
       # Pass to section researcher agent for evidence card extraction
       researcher = create_section_researcher()
       message = f"Research brief:\nQuestions: {json.dumps(brief.questions)}\n\nSearch results:\n{json.dumps(all_results, indent=2)}"
       result = await _invoke_agent(researcher, message)
       return result.cards
   ```

3. **Parallel research orchestration** (add to `backend/app/agent_service.py`):
   ```python
   async def research_all_sections(course_id: str, briefs: list[ResearchBrief]) -> None:
       section_briefs = [b for b in briefs if b.section_position is not None]
       results = await asyncio.gather(*[
           research_section(brief) for brief in section_briefs
       ], return_exceptions=True)
       for brief, result in zip(section_briefs, results):
           if isinstance(result, Exception):
               logger.error(f"Research failed for section {brief.section_position}: {result}")
               continue
           # Save evidence cards to DB
           await save_evidence_cards(course_id, brief.section_position, result)
   ```

4. **DB helper functions** (add to `backend/app/agent_service.py` or new `backend/app/crud.py`):
   - `save_evidence_cards(course_id, section_position, cards)` — bulk insert `EvidenceCard` rows
   - `get_evidence_cards(course_id, section_position)` — query cards for a section
   - `get_research_briefs(course_id)` — query all briefs for a course

### Documentation references
- `asyncio.gather(return_exceptions=True)`: parallel execution with error isolation
- `AsyncTavilyClient.search()`: Phase 0 Tavily section
- Structured output extraction: Phase 0 Deep Agents section
- Bulk insert: `session.add_all([EvidenceCard(...) for card in cards])` then `await session.commit()`

### Verification checklist
- [ ] Section researcher agent returns `EvidenceCardSet` with well-formed cards
- [ ] Each card has: claim, source_url, passage, confidence, source_tier
- [ ] Parallel research runs all sections concurrently
- [ ] Failed sections don't crash the batch (logged, skipped)
- [ ] Evidence cards saved to DB with correct `course_id` and `section_position`
- [ ] Cards retrievable by section position

### Anti-pattern guards
- Do NOT run research sequentially — use `asyncio.gather` for parallelism
- Do NOT let one section's failure crash all research — use `return_exceptions=True`
- Do NOT skip the `retrieved_date` field — set to `date.today()` when saving cards
- Do NOT trust Tavily content blindly — the section researcher agent evaluates and structures it

---

## Phase 4: Verifier Agent

**Goal:** After research, the verifier checks evidence quality per section before writing. Triggers re-research if insufficient.

### Tasks

1. **Verifier agent** (add to `backend/app/agent.py`):
   - Define `VERIFIER_PROMPT` — instructs the agent to:
     - Review evidence cards against the research brief's must-answer questions
     - Check: Does each question have supporting evidence? Are confidence scores justified by passages? Any contradictions between cards?
     - Mark each card as verified or rejected with reasoning
     - If coverage is insufficient (< half questions answered), set `needs_more_research=True` with specific gaps
   - Define structured output:
     ```python
     class CardVerification(BaseModel):
         card_index: int
         verified: bool
         note: str | None = None

     class VerificationResult(BaseModel):
         card_verifications: list[CardVerification]
         needs_more_research: bool
         gaps: list[str]  # unanswered questions or weak areas
     ```
   - Create `create_verifier()` with `ToolStrategy(VerificationResult)`

2. **Verification service function** (add to `backend/app/agent_service.py`):
   ```python
   async def verify_evidence(
       cards: list[EvidenceCard],
       brief: ResearchBrief,
   ) -> VerificationResult:
       verifier = create_verifier()
       message = f"Research brief questions:\n{json.dumps(brief.questions)}\n\nEvidence cards:\n{_format_cards(cards)}"
       result = await _invoke_agent(verifier, message)
       # Update card verified status in DB
       for v in result.card_verifications:
           cards[v.card_index].verified = v.verified
           cards[v.card_index].verification_note = v.note
       await _update_card_verification(cards)
       return result
   ```

3. **Re-research function** (add to `backend/app/agent_service.py`):
   ```python
   async def research_section_targeted(gaps: list[str]) -> list[EvidenceCardItem]:
       """One retry with targeted queries for specific gaps."""
       client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
       all_results = []
       for gap in gaps:
           result = await client.search(gap, max_results=3, search_depth="advanced")
           all_results.extend(result["results"])
       researcher = create_section_researcher()
       message = f"Fill these specific gaps:\n{json.dumps(gaps)}\n\nSearch results:\n{json.dumps(all_results, indent=2)}"
       result = await _invoke_agent(researcher, message)
       return result.cards
   ```

### Documentation references
- Verifier is a no-tool agent — `tools=[]`, just LLM judgment
- Structured output: `ToolStrategy(VerificationResult)` — Phase 0 Deep Agents
- Re-research uses `search_depth="advanced"` for deeper results (2 credits per query)

### Verification checklist
- [ ] Verifier correctly approves well-evidenced cards (real URL, real passage, matching claim)
- [ ] Verifier rejects cards with low confidence or missing passages
- [ ] `needs_more_research` is True when < half questions have evidence
- [ ] `gaps` list contains the specific unanswered questions
- [ ] Re-research produces additional cards targeting the gaps
- [ ] Card `verified` and `verification_note` fields updated in DB

### Anti-pattern guards
- Do NOT give the verifier tools — it should not search, only judge
- Do NOT retry re-research more than once — if still insufficient, proceed with what we have
- Do NOT verify with a different model than writing — use the same `get_model()` for consistency

---

## Phase 5: Writer + Editor + Blackboard

**Goal:** Writer generates evidence-based lessons with citations. Editor polishes and updates the blackboard. Both are blackboard-aware.

### Tasks

1. **Blackboard CRUD functions** (add to `backend/app/agent_service.py` or `crud.py`):
   ```python
   async def create_blackboard(course_id: str) -> Blackboard:
       bb = Blackboard(course_id=course_id)
       session.add(bb)
       await session.commit()
       return bb

   async def get_blackboard(course_id: str) -> Blackboard:
       ...

   async def update_blackboard(blackboard: Blackboard, updates: dict) -> None:
       # Merge updates into existing blackboard JSON fields
       # Validate structure before applying
       ...
   ```

2. **Extend writer agent** (modify in `backend/app/agent.py`):
   - Update `WRITER_PROMPT` to include instructions for:
     - Using verified evidence cards as the basis for all factual claims
     - Citing every factual claim with `[N]` markers
     - Reading the blackboard glossary — don't re-define known terms
     - Reading concept ownership — reference prior sections, don't re-explain
     - Reading coverage map — build on what's covered, don't repeat
   - Writer still returns plain markdown (no structured output) — same as M1
   - The service layer builds the message with evidence cards + blackboard context

3. **Editor agent** (add to `backend/app/agent.py`):
   - Define `EDITOR_PROMPT` — instructs the agent to:
     - Check terminology consistency against blackboard glossary
     - Smooth transitions referencing prior sections
     - Remove repetition of already-covered material
     - Verify `[N]` citation numbers are present for factual claims
     - Generate blackboard updates: new glossary terms, concept ownership, coverage, key points
   - Define structured output:
     ```python
     class BlackboardUpdates(BaseModel):
         new_glossary_terms: dict  # {term: {definition, defined_in_section}}
         new_concept_ownership: dict  # {concept: section_position}
         topics_covered: list[str]
         key_points_summary: str
         new_sources: list[dict]  # [{url, title}]

     class EditorResult(BaseModel):
         edited_content: str  # polished markdown
         blackboard_updates: BlackboardUpdates
     ```
   - Create `create_editor()` with `ToolStrategy(EditorResult)`

4. **Write section service function** (modify in `backend/app/agent_service.py`):
   ```python
   async def write_section(
       cards: list[EvidenceCard],
       blackboard: Blackboard,
       section: Section,
       outline: list[Section],
   ) -> str:
       writer = create_writer()
       verified_cards = [c for c in cards if c.verified]
       message = _build_writer_message(section, outline, verified_cards, blackboard)
       result = await _invoke_agent(writer, message)
       return _extract_content(result)
   ```

5. **Edit section service function** (add to `backend/app/agent_service.py`):
   ```python
   async def edit_section(
       draft: str,
       blackboard: Blackboard,
       cards: list[EvidenceCard],
       section_position: int,
   ) -> EditorResult:
       editor = create_editor()
       message = _build_editor_message(draft, blackboard, cards, section_position)
       result = await _invoke_agent(editor, message)
       return result
   ```

6. **Citation extraction** (add to `backend/app/agent_service.py`):
   ```python
   def extract_citations(content: str, cards: list[EvidenceCard]) -> list[dict]:
       """Map [N] markers in content to evidence card source info."""
       import re
       citation_numbers = set(int(n) for n in re.findall(r'\[(\d+)\]', content))
       citations = []
       for n in sorted(citation_numbers):
           if n <= len(cards):
               card = cards[n - 1]  # 1-indexed
               citations.append({
                   "number": n,
                   "claim": card.claim,
                   "source_url": card.source_url,
                   "source_title": card.source_title,
               })
       return citations
   ```

### Documentation references
- Writer markdown output + `##` parsing: existing `_split_markdown_sections` in M1 `agent_service.py`
- Editor structured output: `ToolStrategy(EditorResult)` — Phase 0 Deep Agents
- JSON merge for blackboard updates: Python dict `.update()` with validation
- Blackboard unique constraint: one per course, Phase 1 migration

### Verification checklist
- [ ] Writer output contains `[1]`, `[2]` citation markers
- [ ] Writer references blackboard terms without re-defining them
- [ ] Editor output is polished markdown with consistent terminology
- [ ] Editor returns `BlackboardUpdates` with new glossary terms, coverage, key points
- [ ] Blackboard updates merge correctly (don't overwrite prior sections' data)
- [ ] Citations extracted correctly mapping `[N]` to evidence card sources
- [ ] Empty blackboard (section 1) works — writer doesn't crash on empty state

### Anti-pattern guards
- Do NOT give writer or editor search tools — they work only with provided evidence
- Do NOT parse citations from writer output to populate evidence — citations reference existing cards
- Do NOT skip blackboard validation — malformed updates should be logged and skipped, not crash the pipeline
- Do NOT modify the writer to use structured output — keep plain markdown for flexibility (matches M1)

---

## Phase 6: Pipeline Orchestration + API

**Goal:** Full pipeline wired together. New API endpoints for evidence, blackboard, and pipeline status. Course creation and generation endpoints updated.

### Tasks

1. **Pipeline status tracking** (add to `backend/app/agent_service.py`):
   - Store pipeline status in-memory (dict keyed by course_id) during generation
   - Update status at each stage: `researching`, `verifying`, `writing`, `editing`, `completed`
   - Track per-section status
   ```python
   # Module-level dict for pipeline tracking
   _pipeline_status: dict[str, dict] = {}

   def update_pipeline_status(course_id: str, section: int | None, stage: str):
       if course_id not in _pipeline_status:
           _pipeline_status[course_id] = {"stage": stage, "current_section": section, "sections": {}}
       _pipeline_status[course_id]["stage"] = stage
       _pipeline_status[course_id]["current_section"] = section
       if section is not None:
           _pipeline_status[course_id]["sections"][section] = stage
   ```

2. **Full pipeline orchestration** (modify `generate_lessons` in `backend/app/agent_service.py`):
   ```python
   async def generate_lessons(course_id: str, session: AsyncSession):
       course = await get_course(course_id, session)
       briefs = await get_research_briefs(course_id, session)

       # Phase: parallel section research
       await update_course_status(course_id, "researching", session)
       update_pipeline_status(course_id, None, "researching")
       await research_all_sections(course_id, briefs)

       # Phase: sequential verify → write → edit
       blackboard = await create_blackboard(course_id, session)

       for section in course.sections:
           cards = await get_evidence_cards(course_id, section.position, session)

           # Verify
           update_pipeline_status(course_id, section.position, "verifying")
           await update_course_status(course_id, "verifying", session)
           brief = next(b for b in briefs if b.section_position == section.position)
           verification = await verify_evidence(cards, brief)

           if verification.needs_more_research:
               new_cards = await research_section_targeted(verification.gaps)
               await save_evidence_cards(course_id, section.position, new_cards)
               cards = await get_evidence_cards(course_id, section.position, session)
               verification = await verify_evidence(cards, brief)

           # Write
           update_pipeline_status(course_id, section.position, "writing")
           await update_course_status(course_id, "writing", session)
           draft = await write_section(cards, blackboard, section, course.sections)

           # Edit
           update_pipeline_status(course_id, section.position, "editing")
           await update_course_status(course_id, "editing", session)
           result = await edit_section(draft, blackboard, cards, section.position)

           # Persist
           citations = extract_citations(result.edited_content, cards)
           await save_section_content(section, result.edited_content, citations, session)
           await update_blackboard(blackboard, result.blackboard_updates, session)
           update_pipeline_status(course_id, section.position, "completed")

       await update_course_status(course_id, "completed", session)
   ```

3. **New API endpoints** (add to `backend/app/routers/courses.py`):
   - `GET /api/courses/{id}/evidence?section=N` — returns evidence cards, optionally filtered by section
   - `GET /api/courses/{id}/blackboard` — returns current blackboard state
   - `GET /api/courses/{id}/pipeline-status` — returns current pipeline progress

4. **Update existing endpoints:**
   - `POST /api/courses` — now runs discovery + grounded planning (from Phase 2)
   - `POST /api/courses/{id}/generate` — now runs full pipeline (research → verify → write → edit)
   - `GET /api/courses/{id}` — include `ungrounded` flag and `citations` in section responses

5. **Update response schemas** (modify `backend/app/schemas.py`):
   - `SectionFull`: add `citations: list[Citation] | None = None`
   - `CourseResponse`: add `ungrounded: bool = False`

### Documentation references
- `asyncio.gather`: Phase 3 parallel pattern
- Endpoint patterns: existing `routers/courses.py` — follow same session injection, error handling
- Pipeline status: in-memory dict (sufficient for single-process; M3 can move to Redis)

### Verification checklist
- [ ] `POST /api/courses` creates course with discovery research + grounded outline
- [ ] `POST /api/courses/{id}/generate` runs full 6-layer pipeline
- [ ] Pipeline status updates at each stage (check via `GET /pipeline-status`)
- [ ] `GET /api/courses/{id}/evidence` returns evidence cards with correct fields
- [ ] `GET /api/courses/{id}/blackboard` returns accumulated blackboard state
- [ ] Section content has `[N]` citations and `citations` JSON is populated
- [ ] Partial failure handled: if section 5 fails, sections 1-4 are saved
- [ ] `Course.ungrounded` is True when discovery research fails

### Anti-pattern guards
- Do NOT store pipeline status in the database — it's ephemeral, in-memory is fine
- Do NOT run the full pipeline synchronously in the endpoint handler — use `BackgroundTasks` or fire-and-forget (the endpoint returns immediately, frontend polls status)
- Do NOT forget to pass `session` through the pipeline — all DB operations need the same session scope
- Do NOT let a blackboard update failure crash the pipeline — log and continue

---

## Phase 7: Frontend — Pipeline Progress, Citations, Evidence

**Goal:** Frontend shows pipeline progress during generation, renders citations in lessons, and provides evidence inspection.

### Tasks

1. **New TypeScript types** (add to `frontend/src/lib/types.ts`):
   ```typescript
   interface EvidenceCard {
     id: string;
     section_position: number;
     claim: string;
     source_url: string;
     source_title: string;
     source_tier: 1 | 2 | 3;
     passage: string;
     retrieved_date: string;
     confidence: number;
     caveat: string | null;
     explanation: string;
     verified: boolean;
     verification_note: string | null;
   }

   interface BlackboardState {
     glossary: Record<string, { definition: string; defined_in_section: number }>;
     concept_ownership: Record<string, number>;
     coverage_map: Record<number, string[]>;
     key_points: Record<number, string>;
     source_log: Array<{ url: string; title: string; sections_used_in: number[] }>;
     open_questions: string[];
   }

   interface PipelineStatus {
     course_id: string;
     stage: string;
     current_section: number | null;
     sections: Record<number, string>;
   }

   interface Citation {
     number: number;
     claim: string;
     source_url: string;
     source_title: string;
   }
   ```
   - Update `Section` type: add `citations: Citation[] | null`
   - Update `Course` type: add `ungrounded: boolean`

2. **New API client functions** (add to `frontend/src/lib/api.ts`):
   - `getEvidence(courseId: string, sectionPosition?: number): Promise<EvidenceCard[]>`
   - `getBlackboard(courseId: string): Promise<BlackboardState>`
   - `getPipelineStatus(courseId: string): Promise<PipelineStatus>`

3. **Pipeline Progress component** (new `frontend/src/components/PipelineProgress.tsx`):
   - Client component (`'use client'`)
   - Polls `/api/courses/{id}/pipeline-status` every 3 seconds
   - Shows section list with stage indicators per section:
     - Researching: search icon / spinner
     - Verifying: check icon / spinner
     - Writing: pencil icon / spinner
     - Editing: polish icon / spinner
     - Completed: green checkmark, clickable to read
   - Overall progress bar at top
   - Stops polling when all sections completed or on error

4. **Citation renderer** (new `frontend/src/components/CitationRenderer.tsx`):
   - Custom `react-markdown` component that transforms `[N]` in text to superscript links
   - Clicking a citation scrolls to the source list at the bottom
   - Source list renders: number, title (hyperlinked), passage excerpt
   - Receives `citations: Citation[]` as prop

5. **Evidence panel** (new `frontend/src/components/EvidencePanel.tsx`):
   - Expandable panel per section in lesson reader
   - Fetches evidence cards for the section on expand
   - Displays each card: claim, source (linked), passage, confidence badge, tier badge
   - Verified/rejected status with note
   - Grouped or listed in order

6. **Update lesson reader** (`frontend/src/app/courses/[id]/learn/page.tsx`):
   - Integrate `CitationRenderer` into markdown rendering
   - Add `EvidencePanel` as expandable section below lesson content
   - Support progressive loading: show completed sections while others are still generating
   - Add "Course Knowledge" tab showing blackboard glossary

7. **Update outline review page** (`frontend/src/app/courses/[id]/page.tsx`):
   - When user clicks "Generate Lessons", show `PipelineProgress` instead of simple loading
   - Completed sections become clickable links to the lesson reader
   - Show `ungrounded` badge if discovery research failed

### Documentation references
- `react-markdown` custom components: replace text nodes matching `\[\d+\]` pattern
- `setInterval` for polling: standard React pattern with `useEffect` cleanup
- `useRouter` from `next/navigation`: for programmatic navigation to lesson reader
- Tailwind styling: existing dark theme classes in `globals.css`

### Verification checklist
- [ ] Pipeline progress shows real-time stage updates during generation
- [ ] Completed sections are clickable before the full course finishes
- [ ] Citations render as superscripts `[1]` in lesson text
- [ ] Clicking a citation scrolls to the sources list
- [ ] Sources list shows title (linked), passage excerpt
- [ ] Evidence panel expands and shows cards with all fields
- [ ] Confidence displayed as percentage badge
- [ ] Source tier displayed as label (Official / Blog / Forum)
- [ ] Blackboard glossary viewable on course page
- [ ] `ungrounded` badge shows when discovery research failed
- [ ] Polling stops when generation completes

### Anti-pattern guards
- Do NOT render citations server-side — use client component for interactivity (scroll, expand)
- Do NOT fetch evidence for all sections at once — fetch per-section on demand (when panel expanded)
- Do NOT forget `useEffect` cleanup for `setInterval` — clear interval on unmount
- Do NOT hardcode poll interval — use a constant (3000ms) that can be adjusted

---

## Phase 8: Integration + Testing

**Goal:** End-to-end flow verified. Tests cover all new agents and pipeline stages. Error handling tested.

### Tasks

1. **Backend unit tests** (add to `backend/tests/`):
   - `test_discovery_researcher.py` — mock Tavily responses, verify TopicBrief structure
   - `test_planner_extended.py` — mock LLM, verify CourseOutlineWithBriefs includes research briefs
   - `test_section_researcher.py` — mock Tavily, verify EvidenceCardSet schema and tier assignment
   - `test_verifier.py` — test with known-good and known-bad evidence sets:
     - Good set: all questions answered, high confidence → approved
     - Bad set: missing coverage, low confidence → needs_more_research + gaps
   - `test_writer_extended.py` — mock agent, verify `[N]` markers in output
   - `test_editor.py` — mock agent, verify EditorResult with blackboard updates
   - `test_blackboard.py` — test CRUD: create, update (merge glossary, coverage), get
   - `test_pipeline.py` — mock all agents, verify full orchestration sequence:
     - Research parallel
     - Verify → write → edit sequential
     - Blackboard accumulates across sections
     - Partial failure: one section fails, others succeed
   - `test_evidence_api.py` — test new API endpoints (evidence, blackboard, pipeline-status)

2. **Update existing tests:**
   - `test_courses.py` — update course creation test to expect new fields (`ungrounded`, `citations`)
   - `conftest.py` — add fixtures for new models (ResearchBrief, EvidenceCard, Blackboard)

3. **Integration test** (marked `@pytest.mark.integration`):
   - End-to-end: topic → discovery → outline → research → verify → write → edit
   - Verify: evidence cards have real URLs, citations appear in content, blackboard populated
   - One focused test: well-known topic, check evidence card URLs resolve

4. **Frontend verification:**
   - Build check: `npm run build` succeeds with no errors
   - Manual test: full flow through UI
   - Citation rendering: verify superscripts appear and source list renders

5. **Error handling verification:**
   - Tavily down: discovery falls back to ungrounded
   - Section research fails: section proceeds with no evidence (marked uncited)
   - Verifier rejects all evidence: writer proceeds with available cards
   - Editor returns bad JSON: blackboard update skipped, content still saved
   - Course status transitions correctly through all states

6. **Clean up:**
   - Add `TAVILY_API_KEY` to `.env.example`
   - Verify `make dev` starts everything cleanly
   - Ensure `.superpowers/` is in `.gitignore`

### Documentation references
- Test patterns: existing `conftest.py` — in-memory SQLite, `autouse` fixture, mocked agents
- `httpx.AsyncClient` with ASGI transport: existing test pattern
- `unittest.mock.patch`: mock `research_section`, `verify_evidence`, etc.

### Verification checklist
- [ ] All unit tests pass: `pytest backend/tests/`
- [ ] Integration test passes with real Tavily + OpenRouter
- [ ] Frontend builds: `npm run build`
- [ ] Full E2E: topic → grounded outline → pipeline progress → citations in lessons
- [ ] Error scenarios tested: Tavily failure, verification rejection, editor malformed output
- [ ] `.env.example` includes `TAVILY_API_KEY`
- [ ] No regressions: M1 functionality still works (regenerate, library, etc.)

### Anti-pattern guards
- Do NOT skip testing the pipeline with mocked agents — this catches orchestration bugs
- Do NOT test LLM output quality — mock the agent responses, test the pipeline logic
- Do NOT forget to test blackboard accumulation across multiple sections
- Do NOT leave real Tavily calls in unit tests — mock them, use integration mark for real calls
