# Course Output Enhancement Design

## Problem

The current course generation pipeline produces uniform, formulaic output:

1. **Rigid section template** — every section follows the same "Why This Matters → Main Content → Examples → Key Takeaways → What Comes Next" skeleton regardless of what the content needs
2. **Low visual variety** — mermaid diagrams and tables are technically supported but rarely generated; output is wall-of-text prose
3. **Uniform depth** — every section is 400-800 words whether it covers "what is a variable" or "how backpropagation works"
4. **Discovery context is wasted** — the discovery phase produces a rich TopicBrief (key concepts, learning progression, open debates, authoritative sources) that the planner consumes, but none of it reaches the writer, editor, or researcher agents

Additionally, the existing Course Style selections (Practical / Beginner / Deep & Technical) are concatenated into the `instructions` string as raw text rather than being interpreted as structured behavioral signals.

## Approach

Prompt engineering + data threading. No new UI components, no new database columns, no new pipeline stages.

## Changes

### 1. Discovery → Worker Context Pipeline

**Goal:** Thread the TopicBrief findings to all downstream agents.

**Mechanism:** The discovery brief is already persisted as a `ResearchBrief` with `section_position=null` and findings in the `findings` field. During the writing and editing phases in `pipeline.py`, load this record and inject its content into agent prompts.

**What each agent receives:**

| Agent | Current Context | Added Context |
|-------|----------------|---------------|
| Researcher | research brief + search results | + discovery findings (authoritative sources to prioritize) |
| Writer | evidence cards + blackboard + outline | + discovery findings (key concepts, learning progression, open debates) |
| Editor | draft + blackboard + evidence cards | + discovery findings (narrative arc, concept relationships) |

**Implementation:** In `pipeline.py`, at the start of the writing phase, query `ResearchBrief` where `course_id=course.id` and `section_position=None`. Pass `brief.findings` as an additional context string into each agent's prompt.

### 2. Course Style as Structured Signals

**Goal:** Make the three style toggles (Practical / Beginner / Deep & Technical) influence agent behavior at every stage, not just appear as text.

**Mechanism:** Parse the style flags from the `instructions` field (or from the course creation payload). Map each to explicit behavioral instructions injected into planner, writer, and editor prompts:

| Style | Planner Behavior | Writer Behavior |
|-------|------------------|-----------------|
| **Practical** | Favor applied topics, include comparison/evaluation sections | Use comparison tables, "how to choose" framing, real-world examples over theory |
| **Beginner Friendly** | Gentler progression, foundational sections first, fewer total sections | Define every term on first use, use analogies, shorter sections, more diagrams for abstract concepts |
| **Deep & Technical** | More sections (10-15), include edge cases and internals | Longer sections on complex topics, architecture diagrams, trade-off analysis, code examples where relevant |

**Combinations work naturally:** "Beginner + Practical" = explain from scratch with real-world examples. "Deep + Practical" = thorough coverage with applied focus.

**How styles are stored today:** The frontend concatenates style instruction strings + English level + extra instructions into a single `instructions` text field (see `buildInstructions()` in `page.tsx:88`). The backend receives this as opaque text.

**Implementation option A (minimal):** Keep the frontend as-is. In the backend, pass the full `instructions` string to each agent prompt but with new prompt framing that tells the LLM to interpret style signals within it (e.g., "The user's instructions may contain style preferences like 'practical examples' or 'assume no prior knowledge' — adapt your output accordingly").

**Implementation option B (cleaner):** Send style IDs as a separate field in the API payload (e.g., `"styles": ["practical", "beginner"]`). Parse them in the backend to generate explicit behavioral prompt sections. This separates structured signals from free-text.

**Recommendation:** Option A for now — it requires zero schema changes and the LLM is perfectly capable of interpreting natural-language style instructions. Option B is a future refinement if needed.

### 3. Writer Prompt Overhaul

**Goal:** Replace the rigid section template with adaptive content guidance.

**Current template (removed):**
```
## Section Title
### Why This Matters
### Main Content
### Examples
### Key Takeaways
### What Comes Next
### References
```

**New approach:** Give the writer a toolkit of content elements and let it choose based on the material:

- **Prose paragraphs** — for explanation and narrative
- **Mermaid diagrams** — for process flows, architecture, state transitions, relationships, decision trees
- **Markdown tables** — for comparisons, feature matrices, option evaluation
- **Blockquote callouts** — for key insights, discovery-sourced debates, important caveats
- **Code blocks** — for technical topics where code examples aid understanding
- **Bullet summaries** — for consolidating key points (not forced into every section)

**Depth guidance:**
- Simple/foundational concepts: 300-500 words, concise treatment
- Standard concepts: 500-800 words, balanced treatment
- Complex/critical concepts: 800-1200+ words, expanded with diagrams and examples
- The writer decides based on: concept complexity, evidence card density, discovery brief signals, and course style

**Mermaid guidance expanded:** Instead of "optional, <15 nodes", the prompt encourages diagrams where they genuinely help and ties usage to the course style signal. Visual-oriented requests get more diagrams; text-focused requests get fewer.

**Structural guidance:** The writer still receives the full course outline and its section's position, so it can:
- Open with context if it's not the first section (referencing what came before)
- Close with a bridge to what comes next (when natural, not forced)
- Avoid repeating what earlier sections covered (using blackboard)

### 4. Placeholder Text Enhancement

**Goal:** Nudge users toward expressing depth/visual preferences in free text.

**Current placeholder:**
```
Any other preferences? e.g. 'Assume I know Python', 'Include code examples'...
```

**New placeholder:**
```
e.g. 'Do a deep dive with lots of diagrams', 'Keep it short and practical', 'Assume I know Python', 'Focus on real-world examples'...
```

**Implementation:** Single string change in `frontend/src/app/page.tsx`.

### 5. Editor Prompt Update

**Goal:** Give the editor discovery context and style signals for narrative coherence.

**What changes:**
- Editor receives the discovery TopicBrief findings (same as writer)
- Editor receives the parsed course style signals
- Editor checks that:
  - Sections reference open debates when relevant to their topic
  - The narrative arc matches the learning progression from discovery
  - Style consistency is maintained (e.g., "Practical" courses don't drift into pure theory)
  - Content format variety is present (not every section is identical wall-of-prose)

## Files Modified

| File | Change |
|------|--------|
| `backend/app/agent.py` | Overhaul WRITER_PROMPT, update EDITOR_PROMPT, add style signal builder |
| `backend/app/pipeline.py` | Load discovery brief, thread to writer/editor/researcher invocations |
| `backend/app/agent_service.py` | Accept and pass discovery context + style signals to agent calls |
| `frontend/src/app/page.tsx` | Update placeholder text string |

## What Stays the Same

- Database schema — no new columns or models
- Pipeline stages — no new agents or phases
- Frontend components — MermaidBlock, CitationRenderer, EvidencePanel, learn page layout all unchanged
- Course creation UI — no new form controls
- API schema — CourseCreate payload unchanged
- Section model — still position/title/summary/content/citations
