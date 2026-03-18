# Milestone 2 — Grounded Content: Design Spec

**Date:** 2026-03-18
**Status:** Approved
**Branch:** milestone-2 (from milestone-1)
**Proves:** Factual claims are traceable to real sources, not hallucinated.

---

## 1. Overview

Milestone 2 transforms agent-learn from model-knowledge-only generation to source-grounded content. Every factual claim in a lesson is backed by an evidence card with a real source, verified before writing, and rendered as a citation in the UI.

The architecture implements a 6-layer grounding pipeline:

1. **Research briefs** — planner generates must-answer questions and source policy per section
2. **Source policy** — tiered source credibility (official docs > blogs > forums)
3. **Evidence cards** — structured handoff format between research and writing
4. **Shared blackboard** — cross-section coordination (glossary, concept ownership, coverage)
5. **STORM-style discovery** — question-first research, not topic-summary research
6. **Verifier + editor** — separate agents for factual checking and coherence

## 2. Pipeline Architecture

### 2.1 End-to-End Flow

```
User submits topic
       │
       ▼
┌─────────────────────┐
│ Discovery Research   │  Broad Tavily searches to map the topic landscape
│ (new agent)          │──► Topic Brief (key concepts, sources, subtopics)
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ Planner              │  Uses topic brief — outline is grounded, not hallucinated
│ (extended from M1)   │──► CourseOutline + ResearchBriefs (per-section questions)
└────────┬────────────┘
         ▼
   User reviews & approves outline
         │
         ▼
┌──────────────────────────────┐
│ Section Research (parallel)   │  All sections researched concurrently
│ (new agent × N sections)     │──► Evidence Cards per section
└────────┬─────────────────────┘
         ▼
   Sequential per section:
         │
  ┌──────┴───────┐    insufficient    ┌────────────────┐
  │   Verifier   │──────────────────►│  Re-research    │
  │   (new)      │                    │  (one retry)    │
  └──────┬───────┘                    └─────────────────┘
         │ pass
         ▼
  ┌──────────────┐
  │    Writer    │◄── verified evidence cards + blackboard state
  │  (extended)  │
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │    Editor    │──► polished lesson + blackboard updates
  │   (new)      │
  └──────┬───────┘
         │
         ▼
   Save section content + citations + update blackboard
   Next section...
```

### 2.2 Two Research Phases

**Phase 1 — Discovery research** (before outline):
- Runs once per course when the user submits a topic
- Broad Tavily searches (3-5 queries) to understand: key concepts, subtopics, authoritative sources, common learning paths, open debates
- Produces a topic brief that the planner uses to create a grounded outline

**Phase 2 — Section research** (after outline approval):
- Runs in parallel for all sections via `asyncio.gather`
- Each researcher gets a research brief (must-answer questions, source policy, scope)
- Produces evidence cards per section

### 2.3 Parallelism Strategy

- **Research:** All sections in parallel (I/O-bound Tavily calls)
- **Verify → Write → Edit:** Sequential per section (blackboard must accumulate)
- Rationale: research briefs from the planner already scope each section. The blackboard matters most during writing (consistent terminology, no repetition), not during research (gathering raw evidence).

## 3. Data Models

### 3.1 New Tables

#### `research_briefs`

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | PK |
| `course_id` | FK → courses | |
| `section_position` | int (nullable) | null = discovery brief, int = section-specific brief |
| `questions` | JSON (list[str]) | Must-answer questions for research |
| `source_policy` | JSON | Source tier preferences, scope constraints |
| `findings` | text (nullable) | Research summary (populated for discovery brief) |
| `created_at` | timestamp | |

#### `evidence_cards`

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | PK |
| `course_id` | FK → courses | |
| `section_position` | int | Which section this evidence supports |
| `claim` | text | The factual assertion |
| `source_url` | text | Where it came from |
| `source_title` | text | Human-readable source name |
| `source_tier` | enum(1,2,3) | 1=official/papers, 2=reputable blogs, 3=forums/repos |
| `passage` | text | Exact excerpt from source supporting the claim |
| `retrieved_date` | date | When evidence was gathered |
| `confidence` | float (0-1) | Research agent's confidence |
| `caveat` | text (nullable) | Qualifications on the claim |
| `explanation` | text | Plain-English summary for the writer |
| `verified` | bool (default false) | Set by verifier agent |
| `verification_note` | text (nullable) | Verifier's reasoning if rejected |
| `created_at` | timestamp | |

#### `blackboard`

One row per course. JSON columns for flexible structured data.

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | PK |
| `course_id` | FK → courses (unique) | One blackboard per course |
| `glossary` | JSON | `{term: {definition, defined_in_section}}` |
| `concept_ownership` | JSON | `{concept: section_position}` |
| `coverage_map` | JSON | `{section_position: [topics_covered]}` |
| `key_points` | JSON | `{section_position: summary_of_what_was_written}` |
| `source_log` | JSON | `[{url, title, sections_used_in}]` |
| `open_questions` | JSON | Unresolved items for later sections |
| `updated_at` | timestamp | |

### 3.2 Changes to Existing Models

**Course.status** — New states added:
- `researching` — discovery or section research in progress
- `verifying` — evidence verification in progress
- `writing` — lesson generation in progress
- `editing` — editorial pass in progress
- Existing: `outline_ready`, `generating` (removed — replaced by granular states), `completed`, `failed`

**Section** — New column:
- `citations` — JSON: `[{number, claim, source_url, source_title}]` for rendering [1] [2] references

### 3.3 Migration

Single Alembic migration adding the three new tables, new Course status enum values, and Section.citations column.

## 4. Agent Architecture

### 4.1 Discovery Researcher (new)

- **Input:** topic + user instructions
- **Tools:** Tavily search
- **Output:** Topic brief — key concepts, subtopics, major sources, common learning progressions, open debates
- **Behavior:** Runs 3-5 broad search queries to map the landscape. Returns structured findings.
- **Structured output:** `ToolStrategy(TopicBrief)`

### 4.2 Planner (extended from M1)

- **Input:** topic + user instructions + discovery brief (topic research findings)
- **Output:** `CourseOutline` (sections with title, summary) + `ResearchBriefs` (per-section must-answer questions, source policy, scope)
- **Structured output:** `ToolStrategy(CourseOutlineWithBriefs)` — extended schema adds `research_briefs` array
- **Change from M1:** Now receives and uses discovery findings. Outline is grounded in real sources.

### 4.3 Section Researcher (new)

- **Input:** Research brief (must-answer questions, source policy, scope)
- **Tools:** Tavily search
- **Output:** Evidence cards (structured JSON array)
- **Structured output:** `ToolStrategy(EvidenceCardSet)`
- **Behavior:** For each must-answer question, runs targeted Tavily searches. Assigns source tiers. Extracts passages. Rates confidence. Runs in parallel for all sections via `asyncio.gather`.

### 4.4 Verifier (new)

- **Input:** Evidence cards for a section + research brief (the questions that should be answered)
- **Output:** Verified/rejected status per card + verification notes + `needs_more_research` flag with gap description
- **No tools** — pure LLM judgment on evidence quality
- **Checks:**
  - Does each must-answer question have supporting evidence?
  - Are confidence scores reasonable given the passages?
  - Any contradictions between cards?
  - Any claims without actual passages?
- **If insufficient:** Returns `needs_more_research: true` with specific gaps. Service layer triggers one re-research attempt.

### 4.5 Writer (extended from M1)

- **Input:** Verified evidence cards + blackboard state + section outline (title, summary)
- **Output:** Lesson markdown with inline citation markers `[1]`, `[2]`
- **No tools** — pure generation
- **Prompt instructions:**
  - Use only verified evidence cards for factual claims
  - Cite every factual claim with `[N]` referencing the evidence card
  - Read the blackboard glossary — don't re-define terms already defined
  - Read concept ownership — reference prior sections instead of re-explaining
  - Read coverage map — know what's been said, build on it
- **Change from M1:** Now evidence-driven instead of model-knowledge-only. Receives blackboard for coherence.

### 4.6 Editor (new)

- **Input:** Written lesson + blackboard state + evidence cards
- **Output:** `{edited_content, blackboard_updates}`
- **No tools** — pure LLM pass
- **Responsibilities:**
  - Consistent terminology with blackboard glossary
  - Smooth transitions referencing prior sections
  - Remove repetition of already-covered material
  - Verify citation numbers match evidence cards
  - Generate blackboard updates: new glossary terms, concept ownership, coverage, key points summary
- **Structured output:** `ToolStrategy(EditorResult)` with `edited_content` string and `blackboard_updates` object

### 4.7 Agent Configuration

All agents use OpenRouter via `init_chat_model` (same as M1). Each agent gets:
- A focused system prompt defining its role and constraints
- Structured output via `ToolStrategy` where applicable
- The Tavily API key is configured via environment variable (`TAVILY_API_KEY`)

## 5. API & Service Layer

### 5.1 New Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/courses/{id}/evidence` | Evidence cards for a course. Optional `?section=N` filter. |
| `GET` | `/api/courses/{id}/blackboard` | Current blackboard state |
| `GET` | `/api/courses/{id}/pipeline-status` | Per-section pipeline stage for progress UI |

### 5.2 Modified Endpoints

**`POST /api/courses`** — Course creation now includes discovery research:
1. Create course record (status: `researching`)
2. Run discovery researcher (Tavily searches on topic)
3. Save topic brief as research_brief with `section_position=null`
4. Run planner with topic brief
5. Save outline + per-section research briefs
6. Update status to `outline_ready`
7. Return course with outline

**`POST /api/courses/{id}/generate`** — Full grounded pipeline:
1. Load research briefs for all sections
2. Set status to `researching`
3. Run section researchers in parallel (`asyncio.gather`)
4. Save evidence cards
5. For each section sequentially:
   - Set status to `verifying` (+ current section position)
   - Run verifier on section's evidence cards
   - If `needs_more_research`: one re-research attempt, re-verify
   - Set status to `writing`
   - Run writer with verified cards + blackboard
   - Set status to `editing`
   - Run editor on draft + blackboard
   - Save section content + citations
   - Update blackboard with editor's updates
6. Set status to `completed`

### 5.3 Pipeline Status Model

```python
class SectionPipelineStatus(BaseModel):
    position: int
    stage: str  # "pending" | "researched" | "verifying" | "writing" | "editing" | "completed" | "failed"

class PipelineStatus(BaseModel):
    course_id: str
    stage: str  # overall: "researching" | "verifying" | "writing" | "editing" | "completed"
    current_section: int | None
    sections: list[SectionPipelineStatus]
```

The frontend polls `/pipeline-status` to render the progress view.

### 5.4 Service Layer Orchestration

```python
async def generate_lessons(course_id: str):
    course = await get_course(course_id)
    briefs = await get_research_briefs(course_id)

    # Phase: parallel section research
    await update_status(course_id, "researching")
    evidence_results = await asyncio.gather(*[
        research_section(brief) for brief in briefs
        if brief.section_position is not None
    ], return_exceptions=True)

    for i, result in enumerate(evidence_results):
        if isinstance(result, Exception):
            log_research_failure(course_id, i, result)
        else:
            await save_evidence_cards(result)

    # Phase: sequential verify → write → edit
    blackboard = await create_blackboard(course_id)

    for section in course.sections:
        cards = await get_evidence_cards(course_id, section.position)

        # Verify
        await update_pipeline_status(course_id, section.position, "verifying")
        verification = await verify_evidence(cards, briefs[section.position])

        if verification.needs_more_research:
            new_cards = await research_section_targeted(verification.gaps)
            cards = merge_cards(cards, new_cards)
            verification = await verify_evidence(cards, briefs[section.position])

        # Write
        await update_pipeline_status(course_id, section.position, "writing")
        draft = await write_section(
            verified_cards=[c for c in cards if c.verified],
            blackboard=blackboard,
            section=section
        )

        # Edit
        await update_pipeline_status(course_id, section.position, "editing")
        result = await edit_section(draft, blackboard, cards)

        # Persist
        await save_section_content(section, result.edited_content, result.citations)
        await update_blackboard(blackboard, result.blackboard_updates)
        await update_pipeline_status(course_id, section.position, "completed")

    await update_status(course_id, "completed")
```

## 6. Frontend Changes

### 6.1 Pipeline Progress View

Replaces the simple "generating..." loading state when the user clicks "Generate Lessons."

- Section list on the left (reuses outline review layout)
- Each section shows its current pipeline stage with a status indicator
- Stages: researching → verifying → writing → editing → completed
- Completed sections become clickable immediately (progressive reading)
- Polls `/api/courses/{id}/pipeline-status` every 2-3 seconds

### 6.2 Citation Rendering

Lessons render with superscript citation numbers:
- Writer outputs `[1]`, `[2]` inline in markdown
- React-markdown custom renderer converts these to superscript links
- Clicking a citation scrolls to the sources list at the bottom of the section
- Each source entry shows: number, title (hyperlinked to URL), relevant passage excerpt

### 6.3 Evidence Inspection Panel

Each section in the lesson reader gets an expandable "Evidence" panel:
- Shows all evidence cards that supported the section
- Each card displays: claim, source (linked), passage excerpt, confidence badge, source tier badge
- Verification status with verifier's note
- Grouped by the must-answer questions from the research brief

### 6.4 Blackboard View

A "Course Knowledge" tab on the course page showing:
- Glossary of terms defined across the course
- Concept ownership map (which section owns what)
- Useful as a learner reference, not just debugging

### 6.5 New TypeScript Types

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

interface ResearchBrief {
  id: string;
  section_position: number | null;
  questions: string[];
  source_policy: Record<string, unknown>;
  findings: string | null;
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
  sections: Array<{ position: number; stage: string }>;
}

interface Citation {
  number: number;
  claim: string;
  source_url: string;
  source_title: string;
}
```

### 6.6 New API Client Functions

```typescript
getEvidence(courseId: string, sectionPosition?: number): Promise<EvidenceCard[]>
getBlackboard(courseId: string): Promise<BlackboardState>
getPipelineStatus(courseId: string): Promise<PipelineStatus>
```

### 6.7 Pages Affected

- `/courses/[id]/page.tsx` — Minor: show research briefs if available on outline review
- `/courses/[id]/learn/page.tsx` — Major: citation rendering, evidence panel, progressive section loading
- New component: `PipelineProgress` — stage-by-stage progress during generation
- New component: `EvidencePanel` — expandable evidence cards per section
- New component: `CitationRenderer` — custom react-markdown plugin for `[N]` superscripts

## 7. Error Handling & Recovery

### 7.1 Per-Stage Failure Modes

**Discovery research fails (Tavily down, no results):**
- Fall back to M1 behavior — planner creates outline from model knowledge
- Flag course as `ungrounded` so UI can indicate "Sources unavailable"
- User can still proceed

**Section research fails (partial):**
- `asyncio.gather(return_exceptions=True)` — one section failing doesn't kill the batch
- Failed sections proceed with no evidence cards
- Verifier sees empty cards, flags as needs-research
- One re-research retry with simplified queries
- If still empty: writer proceeds with model knowledge, section marked `uncited`

**Verification rejects evidence:**
- If coverage below threshold (fewer than half must-answer questions have evidence), triggers targeted re-research
- One retry — if still insufficient, writer proceeds with available evidence
- Verification notes persisted for transparency

**Writer or editor fails (LLM error):**
- Retry once, then mark section as `failed`
- Other sections continue — partial course preserved
- Failed sections can be retried individually via existing regenerate flow

**Blackboard update fails (bad JSON from editor):**
- Validate blackboard updates against schema before applying
- If validation fails, skip update for that section, log warning, continue
- Blackboard is a coordination aid, not the source of truth for content

### 7.2 Course Status Progression

```
outline_ready → researching → verifying → writing → editing → completed
                                                                  │
                          any stage can ──────────────────► failed
                          (partial results preserved)
```

### 7.3 Key Principle

Never lose completed work. If section 7 fails, sections 1-6 are saved and readable. The pipeline is resumable.

## 8. Testing Strategy

### 8.1 Unit Tests (Mocked Agents)

- **Discovery researcher** — mock Tavily, verify topic brief structure
- **Planner** — mock LLM, verify research briefs included alongside outline
- **Section researcher** — mock Tavily, verify evidence card schema and source tier assignment
- **Verifier** — known-good and known-bad evidence sets, verify approve/reject logic and gap detection
- **Writer** — feed verified cards + blackboard, verify `[N]` citation markers present, no repetition of blackboard-owned concepts
- **Editor** — feed draft + blackboard, verify blackboard updates returned and content cleaned
- **Blackboard** — test update logic: glossary merges, concept ownership, coverage accumulation
- **Pipeline orchestration** — mock all agents, verify correct sequence and data flow

### 8.2 Integration Tests

- End-to-end: topic → discovery → outline → research → verify → write → edit → verify citations exist
- Marked with `@pytest.mark.integration` (not in CI by default)
- Focused test: well-known topic, verify evidence cards have real URLs matching claims

### 8.3 Frontend Tests

- Citation rendering: `[1]` renders as superscript, click scrolls to source
- Pipeline progress: mock status endpoint, verify stage indicators update
- Evidence panel: verify cards render with all fields

## 9. Configuration

### 9.1 New Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `TAVILY_API_KEY` | Tavily search API key | Required for grounded generation |

### 9.2 Existing (Unchanged)

| Variable | Purpose | Default |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | LLM calls via OpenRouter | Required |
| `OPENROUTER_MODEL` | Model for all agents | `anthropic/claude-sonnet-4` |
| `DATABASE_URL` | Database connection | SQLite default for dev |

## 10. Dependencies

### 10.1 New Python Packages

- `tavily-python` — Tavily search API client

### 10.2 Existing (Unchanged)

- FastAPI, SQLAlchemy, Pydantic, langchain, httpx, uvicorn
- Next.js, React, react-markdown, Tailwind CSS
