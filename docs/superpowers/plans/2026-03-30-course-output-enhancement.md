# Course Output Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve course output quality by threading discovery context to worker agents, overhauling the writer/editor prompts for adaptive content, and making existing style selections actually influence output.

**Architecture:** No new DB columns, models, or pipeline stages. The discovery `TopicBrief` is serialized into the existing `ResearchBrief.findings` text field (currently storing a placeholder string). Prompts are rewritten to accept discovery context and produce varied, adaptive content. The frontend gets a placeholder text update.

**Tech Stack:** Python (FastAPI, SQLAlchemy, langchain), Next.js/React, pytest

---

### Task 1: Persist Discovery TopicBrief in Findings Field

**Files:**
- Modify: `backend/app/agent_service.py:1476-1484`
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Currently the discovery brief row saves `findings="Discovery research completed successfully"` — a useless placeholder. We need to serialize the actual `TopicBrief` there.

- [ ] **Step 1: Write the failing test**

Add a test to `backend/tests/test_phase5_writer_editor_blackboard.py` that verifies `run_discover_and_plan` saves the TopicBrief JSON into the discovery brief's findings field.

```python
@pytest.mark.asyncio
async def test_discovery_brief_persists_topic_brief(setup_db, db_session):
    """Discovery brief should contain serialized TopicBrief, not a placeholder string."""
    import json
    from app.models import Course, ResearchBrief
    from sqlalchemy import select

    # Create a test course
    course = Course(topic="Test Topic", user_id="00000000-0000-0000-0000-000000000001", status="researching")
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)

    # Mock generate_outline to return a known TopicBrief
    fake_topic_brief_data = {
        "key_concepts": ["concept_a", "concept_b"],
        "subtopics": ["sub1", "sub2"],
        "authoritative_sources": ["https://example.com"],
        "learning_progression": "Start with concept_a, then move to concept_b",
        "open_debates": ["Is concept_a better than concept_b?"],
    }

    from unittest.mock import AsyncMock, patch
    from app.agent import CourseOutlineWithBriefs, OutlineSection, ResearchBriefItem, TopicBrief

    fake_outline = CourseOutlineWithBriefs(
        sections=[OutlineSection(position=1, title="Section 1", summary="Summary 1")],
        research_briefs=[ResearchBriefItem(
            section_position=1,
            questions=["What is concept_a?"],
            source_policy={"preferred_tiers": [1, 2], "scope": "basics", "out_of_scope": ""},
        )],
    )

    fake_brief = TopicBrief(
        key_concepts=fake_topic_brief_data["key_concepts"],
        subtopics=fake_topic_brief_data["subtopics"],
        authoritative_sources=fake_topic_brief_data["authoritative_sources"],
        learning_progression=fake_topic_brief_data["learning_progression"],
        open_debates=fake_topic_brief_data["open_debates"],
        raw_search_results=[],
    )

    with patch("app.agent_service.generate_outline", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = (fake_outline, False)

        from app.agent_service import run_discover_and_plan
        result = await run_discover_and_plan(
            course.id, db_session, "openai", "gpt-4", {"api_key": "test"},
        )

    # Fetch the discovery brief (section_position=None)
    brief_result = await db_session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course.id,
            ResearchBrief.section_position == None,
        )
    )
    discovery_brief = brief_result.scalar_one_or_none()
    assert discovery_brief is not None
    assert discovery_brief.findings is not None

    # Should be valid JSON containing TopicBrief data, not the placeholder
    findings = json.loads(discovery_brief.findings)
    assert "key_concepts" in findings
    assert findings["key_concepts"] == ["concept_a", "concept_b"]
    assert "learning_progression" in findings
    assert "open_debates" in findings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_discovery_brief_persists_topic_brief -v`
Expected: FAIL — findings contains the placeholder string, not JSON

- [ ] **Step 3: Implement — pass TopicBrief through generate_outline return**

The `TopicBrief` is created inside `discover_topic()` (line 381) and returned. `generate_outline()` (line 425) calls `discover_topic()` and passes the brief to the planner, but doesn't return it. We need `generate_outline` to return the brief too so `run_discover_and_plan` can persist it.

In `backend/app/agent_service.py`, modify `generate_outline` to return the `TopicBrief`:

Change the return type and return statement at the end of `generate_outline` (around line 488):

```python
async def generate_outline(
    topic: str,
    instructions: str | None = None,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
    on_event: EventCallback | None = None,
    user_id: str = "",
    current_outline: Sequence | None = None,
    academic_options: dict | None = None,
) -> tuple[CourseOutlineWithBriefs, bool, TopicBrief | None]:
    """Invoke discovery research + planner to generate a grounded course outline.

    Returns (CourseOutlineWithBriefs, ungrounded_flag, topic_brief_or_None).
    If discovery fails, falls back to ungrounded planning (ungrounded=True).
    """
    # ... existing code up to planner invocation stays the same ...
    # At the end, change:
    return outline, ungrounded, topic_brief
```

The three early-return/fallback paths need to propagate `topic_brief` (which may be `None`).

Then in `run_discover_and_plan` (around line 1441), unpack the third return value and serialize it:

```python
    outline_with_briefs, ungrounded, topic_brief = await generate_outline(
        course.topic, course.instructions, provider, model, credentials, extra_fields,
        search_provider, search_credentials, user_id=user_id,
        academic_options=academic_options,
    )
```

And where the discovery brief is saved (around line 1476-1484):

```python
    if not ungrounded:
        discovery_findings = topic_brief.model_dump_json() if topic_brief else "{}"
        discovery_brief = ResearchBrief(
            course_id=course.id,
            section_position=None,
            questions=[],
            source_policy={},
            findings=discovery_findings,
        )
        session.add(discovery_brief)
```

- [ ] **Step 4: Fix any callers of generate_outline**

Search for all callers of `generate_outline` and update them to handle the new 3-tuple return. The main callers are:
- `run_discover_and_plan` (already updated above)
- The SSE streaming endpoint in `backend/app/routers/courses.py` — search for `generate_outline` there and update the unpacking.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_discovery_brief_persists_topic_brief -v`
Expected: PASS

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests pass. Fix any failures caused by the `generate_outline` return type change.

- [ ] **Step 7: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent_service.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: persist TopicBrief in discovery brief findings field"
```

---

### Task 2: Load Discovery Context in Writer Pipeline

**Files:**
- Modify: `backend/app/agent_service.py:1231-1296` (write_section function)
- Modify: `backend/app/agent_service.py:1684-1768` (run_write_section function)
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Thread the discovery brief's findings into the writer's prompt message so it has topic-level context.

- [ ] **Step 1: Write the failing test**

Add a test that verifies the writer receives discovery context in its message.

```python
@pytest.mark.asyncio
async def test_write_section_includes_discovery_context(setup_db, db_session, course_with_cards):
    """Writer should receive discovery context when a discovery brief exists."""
    import json
    from unittest.mock import AsyncMock, patch, ANY
    from app.models import ResearchBrief
    from app.agent_service import run_write_section

    course, section, cards = course_with_cards

    # Create a discovery brief with real TopicBrief data
    discovery_data = {
        "key_concepts": ["gradient descent", "backpropagation"],
        "learning_progression": "Start with forward pass, then loss, then gradients",
        "open_debates": ["SGD vs Adam for generalization"],
    }
    discovery_brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings=json.dumps(discovery_data),
    )
    db_session.add(discovery_brief)
    await db_session.commit()

    captured_messages = []

    async def mock_ainvoke(messages):
        captured_messages.extend(messages)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = f"## {section.title}\n\nSome content [1]."
        return resp

    with patch("app.agent_service.provider_service.build_chat_model") as mock_build:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = mock_ainvoke
        mock_build.return_value = mock_llm

        result = await run_write_section(
            course.id, section.position, db_session, "openai", "gpt-4", {"api_key": "test"},
        )

    # The HumanMessage should contain discovery context
    human_msg = captured_messages[-1]
    msg_content = human_msg.content if hasattr(human_msg, "content") else str(human_msg)
    assert "DISCOVERY CONTEXT" in msg_content
    assert "gradient descent" in msg_content
    assert "SGD vs Adam" in msg_content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_write_section_includes_discovery_context -v`
Expected: FAIL — no "DISCOVERY CONTEXT" in the message

- [ ] **Step 3: Create helper to load and format discovery context**

Add a helper function in `backend/app/agent_service.py` near the other format helpers (around line 1219):

```python
async def _load_discovery_context(course_id, session: AsyncSession) -> str:
    """Load discovery brief findings and format for writer/editor prompts."""
    result = await session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course_id,
            ResearchBrief.section_position == None,
        )
    )
    brief = result.scalar_one_or_none()
    if not brief or not brief.findings:
        return ""

    try:
        data = json.loads(brief.findings)
    except (json.JSONDecodeError, ValueError):
        return ""

    parts = []
    if data.get("key_concepts"):
        parts.append("KEY CONCEPTS: " + ", ".join(data["key_concepts"]))
    if data.get("learning_progression"):
        parts.append("LEARNING PROGRESSION: " + data["learning_progression"])
    if data.get("open_debates"):
        parts.append("OPEN DEBATES:\n  - " + "\n  - ".join(data["open_debates"]))
    if data.get("authoritative_sources"):
        parts.append("AUTHORITATIVE SOURCES:\n  - " + "\n  - ".join(data["authoritative_sources"][:5]))
    if data.get("subtopics"):
        parts.append("SUBTOPICS: " + ", ".join(data["subtopics"]))

    return "\n\n".join(parts) if parts else ""
```

- [ ] **Step 4: Inject discovery context into write_section message**

In `run_write_section` (around line 1730, after fetching blackboard), load the discovery context and pass it to `write_section`. Modify `write_section` to accept and include it.

Update `write_section` signature:

```python
async def write_section(
    cards: list[EvidenceCard],
    blackboard: Blackboard | None,
    section,
    outline: Sequence,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    discovery_context: str = "",
) -> str:
```

And in the message construction (around line 1270-1278), add the discovery context block:

```python
    message = (
        f"Write the lesson content for this section:\n\n"
        f"Section title: {sec_title}\n"
        f"Section summary: {sec_summary}\n\n"
        f"--- FULL COURSE OUTLINE (for context) ---\n{outline_text}\n\n"
        f"--- VERIFIED EVIDENCE CARDS ---\n{cards_text}\n\n"
        f"--- BLACKBOARD (shared course knowledge) ---\n{blackboard_text}\n\n"
    )
    if discovery_context:
        message += f"--- DISCOVERY CONTEXT (topic-level intelligence from research) ---\n{discovery_context}\n\n"
    message += f"Write the section now. Start with ## {sec_title}"
```

In `run_write_section` (around line 1737), load and pass the context:

```python
    # Load discovery context
    discovery_context = await _load_discovery_context(course_id, session)

    # Run writer with retry on empty output
    max_write_attempts = 3
    draft = ""
    for attempt in range(1, max_write_attempts + 1):
        draft = await write_section(
            cards, blackboard, section, list(course.sections), session,
            provider, model, credentials, extra_fields,
            discovery_context=discovery_context,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_write_section_includes_discovery_context -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass. Existing writer tests may need the new `discovery_context` param defaulting to `""`.

- [ ] **Step 7: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent_service.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: thread discovery context into writer pipeline"
```

---

### Task 3: Load Discovery Context in Editor Pipeline

**Files:**
- Modify: `backend/app/agent_service.py:1304-1360` (edit_section function)
- Modify: `backend/app/agent_service.py:1771-1888` (run_edit_section function)
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Same pattern as Task 2, but for the editor.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_edit_section_includes_discovery_context(setup_db, db_session, course_with_cards):
    """Editor should receive discovery context when a discovery brief exists."""
    import json
    from unittest.mock import AsyncMock, patch
    from app.models import ResearchBrief
    from app.agent_service import run_edit_section
    from app.agent import EditorResult, BlackboardUpdates

    course, section, cards = course_with_cards

    # Give the section draft content so the editor has something to edit
    section.content = "## Test Section\n\nSome draft content [1]."
    await db_session.commit()

    # Create a discovery brief
    discovery_data = {
        "key_concepts": ["neural networks"],
        "open_debates": ["CNN vs Transformer for vision"],
    }
    discovery_brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings=json.dumps(discovery_data),
    )
    db_session.add(discovery_brief)
    await db_session.commit()

    captured_messages = []

    fake_result = EditorResult(
        edited_content="## Test Section\n\nEdited content [1].",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=["test"],
            key_points_summary="Test summary",
            new_sources=[],
        ),
    )

    with patch("app.agent_service._invoke_agent", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = fake_result

        result = await run_edit_section(
            course.id, section.position, db_session, "openai", "gpt-4", {"api_key": "test"},
        )

    # Check the message passed to the editor agent
    call_args = mock_invoke.call_args
    message = call_args[0][1]  # second positional arg is the message string
    assert "DISCOVERY CONTEXT" in message
    assert "neural networks" in message
    assert "CNN vs Transformer" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_edit_section_includes_discovery_context -v`
Expected: FAIL

- [ ] **Step 3: Inject discovery context into edit_section**

Update `edit_section` signature to accept `discovery_context`:

```python
async def edit_section(
    draft: str,
    blackboard: Blackboard | None,
    cards: list[EvidenceCard],
    section_position: int,
    session: AsyncSession,
    provider: str = "",
    model: str = "",
    credentials: dict | None = None,
    extra_fields: dict | None = None,
    discovery_context: str = "",
) -> EditorResult:
```

Add the discovery context to the editor message (around line 1329-1336):

```python
    message = (
        f"Edit the following draft for section {section_position}.\n\n"
        f"--- DRAFT ---\n{draft}\n\n"
        f"--- BLACKBOARD (shared course knowledge) ---\n{blackboard_text}\n\n"
        f"--- EVIDENCE CARDS ---\n{cards_text}\n\n"
    )
    if discovery_context:
        message += f"--- DISCOVERY CONTEXT (topic-level intelligence from research) ---\n{discovery_context}\n\n"
    message += (
        f"Section position: {section_position}\n\n"
        f"Polish the draft, check citations, and generate blackboard updates."
    )
```

In `run_edit_section` (around line 1830), load and pass the context:

```python
    # Load discovery context
    discovery_context = await _load_discovery_context(course_id, session)

    # Run editor with retry
    # ... existing retry loop, passing discovery_context to edit_section:
        editor_result = await edit_section(
            draft, blackboard, cards, section_position, session,
            provider, model, credentials, extra_fields,
            discovery_context=discovery_context,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_edit_section_includes_discovery_context -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent_service.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: thread discovery context into editor pipeline"
```

---

### Task 4: Overhaul WRITER_PROMPT

**Files:**
- Modify: `backend/app/agent.py:138-175`
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Replace the rigid section template with adaptive content guidance.

- [ ] **Step 1: Write a test that verifies the prompt no longer contains the rigid template**

```python
def test_writer_prompt_no_rigid_template():
    """Writer prompt should not enforce the rigid Why This Matters / Key Takeaways template."""
    from app.agent import WRITER_PROMPT
    assert "Why This Matters" not in WRITER_PROMPT
    assert "Key Takeaways" not in WRITER_PROMPT
    assert "What Comes Next" not in WRITER_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_writer_prompt_no_rigid_template -v`
Expected: FAIL — current prompt contains all three

- [ ] **Step 3: Replace WRITER_PROMPT**

In `backend/app/agent.py`, replace the entire `WRITER_PROMPT` (lines 138-175) with:

```python
WRITER_PROMPT = """You are a course lesson writer. You will receive a single section to write, along with verified evidence cards, a blackboard representing shared course knowledge, and optionally discovery context about the broader topic.

IMPORTANT: Start the section with a level-2 heading using the EXACT section title provided:
## Section Title Here

ADAPTIVE STRUCTURE:
Do NOT follow a fixed template. Structure each section based on what the content needs. Choose from these elements as appropriate:

- **Prose paragraphs** — for explanation and narrative flow
- **Mermaid diagrams** (```mermaid code blocks) — for process flows, architecture, state transitions, relationships, decision trees. Keep under 15 nodes with clear labels and no styling directives.
- **Markdown tables** — for comparisons, feature matrices, option evaluation
- **Blockquote callouts** (> **Key insight:** ...) — for important observations, open debates, or caveats from discovery research
- **Code blocks** — for technical topics where examples aid understanding
- **Bullet summaries** — when consolidating key points (not required in every section)

DEPTH GUIDANCE:
- Simple or foundational concepts: 300-500 words, concise treatment
- Standard concepts: 500-800 words, balanced treatment
- Complex or critical concepts: 800-1200+ words, expanded with diagrams, tables, and examples
- Let the concept's complexity and the density of evidence cards guide your depth

DISCOVERY CONTEXT:
If you receive discovery context (key concepts, learning progression, open debates, authoritative sources), use it to:
- Connect this section to the broader topic narrative
- Surface relevant open debates or controversies when they apply to this section's content
- Reference the learning progression so the reader understands where this section fits
- Prioritize claims supported by authoritative sources identified in discovery

USER INSTRUCTIONS:
If user instructions are present, they may contain style preferences. Adapt accordingly:
- "Practical" / "real-world" → favor comparison tables, applied examples, "how to choose" framing
- "Beginner" / "from the ground up" → define every term on first use, use analogies, more diagrams for abstract concepts
- "Deep" / "technical" → include edge cases, trade-offs, architecture details, code examples

EVIDENCE AND CITATIONS:
- You will receive a numbered list of verified evidence cards. Use them as the basis for ALL factual claims.
- Cite every factual claim with [N] markers (1-indexed, matching the card order provided).
  Example: "Python was created by Guido van Rossum in 1991 [1]."
- Do NOT fabricate claims without evidence card support.
- If an evidence card has a caveat, mention it naturally in the text.

For evidence cards marked as academic sources, naturally incorporate the author(s) and year into the text before the citation marker. Example: "According to Smith et al. (2023), transformers outperform RNNs on sequence tasks [3]."

BLACKBOARD AWARENESS:
- You will receive a blackboard with glossary, concept ownership, and coverage map.
- Glossary: Do NOT re-define terms already in the glossary. Use them directly and reference where they were introduced if helpful.
- Concept ownership: Do NOT re-explain concepts owned by earlier sections. Instead, reference the prior section (e.g., "As we saw in Section 2, ...").
- Coverage map: Build on topics already covered. Do NOT repeat content from earlier sections.
- If the blackboard is empty (first section), you have full freedom to define terms and introduce concepts.

NARRATIVE FLOW:
- If this is not the first section, open with a brief connection to what came before (when natural, not forced)
- If this is not the last section, close with a natural bridge to what comes next (one sentence, not a formulaic "What Comes Next" heading)
- Use the full course outline to understand your section's position in the narrative

Guidelines:
- Write in a conversational but informative tone
- Use markdown formatting: headings (### for subsections), bold, code blocks, lists
- Make examples practical and concrete, not abstract

You will receive the full course outline for context so you can maintain coherence.
Output ONLY the markdown content for the requested section. Do NOT output JSON or structured data."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_writer_prompt_no_rigid_template -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass. The writer prompt is used as a system message — no code depends on its exact content.

- [ ] **Step 6: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: overhaul writer prompt for adaptive content structure"
```

---

### Task 5: Update EDITOR_PROMPT with Discovery Awareness

**Files:**
- Modify: `backend/app/agent.py:290-315`
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Update the editor prompt to leverage discovery context and check for content variety.

- [ ] **Step 1: Write a test**

```python
def test_editor_prompt_references_discovery():
    """Editor prompt should instruct the editor to use discovery context."""
    from app.agent import EDITOR_PROMPT
    assert "discovery" in EDITOR_PROMPT.lower() or "DISCOVERY" in EDITOR_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_editor_prompt_references_discovery -v`
Expected: FAIL

- [ ] **Step 3: Update EDITOR_PROMPT**

In `backend/app/agent.py`, replace `EDITOR_PROMPT` (lines 290-315) with:

```python
EDITOR_PROMPT = """You are a course lesson editor. You receive a draft section, the course blackboard, evidence cards, the section position, and optionally discovery context about the broader topic.

Your job is to polish the draft and generate blackboard updates.

EDITING TASKS:
1. **Terminology consistency**: Check that terms used in the draft match the blackboard glossary definitions. If a term is used differently, correct it. If a new term is introduced, note it in blackboard updates.
2. **Transitions**: Smooth transitions referencing prior sections. If the blackboard shows prior content, add connecting phrases (e.g., "Building on the concepts from Section 2...").
3. **Repetition removal**: If the coverage map shows a topic was already covered in a prior section, remove redundant explanations. Replace with brief references to the prior section.
4. **Citation verification**: Verify that [N] citation numbers are present for factual claims. If a factual claim lacks a citation, add one if a matching evidence card exists, or flag it.
5. **Quality polish**: Fix awkward phrasing, improve flow, ensure the section reads well as part of the larger course.
6. **Content variety**: Ensure the section uses appropriate content formats (prose, tables, diagrams, callouts) rather than being a uniform wall of text. If the draft is entirely prose and the content would benefit from a comparison table or diagram, add one.

DISCOVERY CONTEXT:
If you receive discovery context, use it to:
- Ensure the section references relevant open debates when they apply to its topic
- Check that the narrative arc matches the learning progression from discovery
- Verify style consistency — if user instructions indicate "practical", the section should not drift into pure theory
- Surface connections between this section's content and the broader topic landscape

BLACKBOARD UPDATES:
After editing, generate updates for the blackboard:
- new_glossary_terms: Any new terms defined in this section. Format: {term: {definition: "...", defined_in_section: N}}
- new_concept_ownership: Concepts this section is the primary owner of. Format: {concept: section_position}
- topics_covered: List of topics/subtopics covered in this section.
- key_points_summary: A 1-2 sentence summary of the key points from this section.
- new_sources: List of new sources cited. Format: [{url: "...", title: "..."}]

Output a structured EditorResult with the edited content and blackboard updates.

After the main content, if the section cites any academic evidence cards (those with is_academic=True), append a "## References" section listing only the academic papers. Format each entry in APA style:

[N] Last, F., Last, F., & Last, F. (Year). Title. *Venue*. DOI_URL

Only include papers actually cited with [N] markers in the section. If no academic papers are cited, do not add a References section."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_editor_prompt_references_discovery -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: update editor prompt with discovery awareness and content variety"
```

---

### Task 6: Include User Instructions in Writer/Editor Messages

**Files:**
- Modify: `backend/app/agent_service.py:1231-1296` (write_section)
- Modify: `backend/app/agent_service.py:1684-1768` (run_write_section)
- Modify: `backend/app/agent_service.py:1771-1888` (run_edit_section)
- Test: `backend/tests/test_phase5_writer_editor_blackboard.py`

Currently the writer/editor don't receive the course's `instructions` field (which contains the style selections). We need to pass it through.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_write_section_includes_user_instructions(setup_db, db_session, course_with_cards):
    """Writer should receive the course's user instructions for style adaptation."""
    from unittest.mock import AsyncMock, patch
    from app.agent_service import run_write_section

    course, section, cards = course_with_cards

    # Set instructions on the course
    course.instructions = "Focus on practical examples and real-world applications. Explain concepts from the ground up."
    await db_session.commit()

    captured_messages = []

    async def mock_ainvoke(messages):
        captured_messages.extend(messages)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = f"## {section.title}\n\nContent [1]."
        return resp

    with patch("app.agent_service.provider_service.build_chat_model") as mock_build:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = mock_ainvoke
        mock_build.return_value = mock_llm

        await run_write_section(
            course.id, section.position, db_session, "openai", "gpt-4", {"api_key": "test"},
        )

    human_msg = captured_messages[-1]
    msg_content = human_msg.content if hasattr(human_msg, "content") else str(human_msg)
    assert "USER INSTRUCTIONS" in msg_content
    assert "practical examples" in msg_content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_write_section_includes_user_instructions -v`
Expected: FAIL

- [ ] **Step 3: Pass course instructions into write_section and edit_section**

In `write_section`, add `user_instructions: str = ""` parameter. In the message, add:

```python
    if user_instructions:
        message += f"--- USER INSTRUCTIONS (style preferences) ---\n{user_instructions}\n\n"
```

In `run_write_section`, pass `course.instructions`:

```python
    draft = await write_section(
        cards, blackboard, section, list(course.sections), session,
        provider, model, credentials, extra_fields,
        discovery_context=discovery_context,
        user_instructions=course.instructions or "",
    )
```

Do the same for `edit_section` / `run_edit_section`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_phase5_writer_editor_blackboard.py::test_write_section_includes_user_instructions -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/app/agent_service.py backend/tests/test_phase5_writer_editor_blackboard.py
git commit -m "feat: pass user instructions to writer and editor for style adaptation"
```

---

### Task 7: Update Frontend Placeholder Text

**Files:**
- Modify: `frontend/src/app/page.tsx:299`

Single line change — update the placeholder to nudge users toward depth/visual preferences.

- [ ] **Step 1: Update the placeholder**

In `frontend/src/app/page.tsx` line 299, change:

```tsx
placeholder="Any other preferences? e.g. &quot;Assume I know Python&quot;, &quot;Include code examples&quot;..."
```

to:

```tsx
placeholder="e.g. &quot;Do a deep dive with lots of diagrams&quot;, &quot;Keep it short and practical&quot;, &quot;Assume I know Python&quot;, &quot;Focus on real-world examples&quot;..."
```

- [ ] **Step 2: Verify the change visually**

Run: `cd /Users/sumo/agent-learn && grep -n 'placeholder=' frontend/src/app/page.tsx | head -5`
Expected: The new placeholder text appears

- [ ] **Step 3: Commit**

```bash
cd /Users/sumo/agent-learn
git add frontend/src/app/page.tsx
git commit -m "feat: update instructions placeholder to suggest depth and visual preferences"
```

---

### Task 8: Integration Test — Full Pipeline with Discovery Context

**Files:**
- Test: `backend/tests/test_pipeline.py`

Add a test that validates the full pipeline threads discovery context end-to-end.

- [ ] **Step 1: Write the integration test**

```python
@pytest.mark.asyncio
async def test_pipeline_threads_discovery_context(setup_db, seeded):
    """Full pipeline should load discovery brief and pass to writer/editor."""
    import json
    from unittest.mock import AsyncMock, patch, call
    from app.models import ResearchBrief
    from app.database import async_session

    course_id, job_id, positions = seeded

    # Insert a discovery brief with real findings
    async with async_session() as session:
        discovery_brief = ResearchBrief(
            course_id=course_id,
            section_position=None,
            questions=[],
            source_policy={},
            findings=json.dumps({
                "key_concepts": ["test_concept"],
                "learning_progression": "concept first, then application",
                "open_debates": ["methodology debate"],
            }),
        )
        session.add(discovery_brief)
        await session.commit()

    # Track what write_section receives
    write_calls = []
    original_write = None

    async def spy_write_section(*args, **kwargs):
        write_calls.append(kwargs.get("discovery_context", ""))
        # Return a minimal draft
        return f"## Section\n\nContent [1]."

    with patch("app.agent_service.write_section", side_effect=spy_write_section):
        with patch("app.pipeline._verify_section", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = {}
            with patch("app.pipeline._edit_section", new_callable=AsyncMock) as mock_edit:
                mock_edit.return_value = {}

                from app.pipeline import run_pipeline, CHECKPOINT_RESEARCHED
                # Start from CHECKPOINT_RESEARCHED to skip planning/research
                await run_pipeline(
                    job_id, course_id, CHECKPOINT_RESEARCHED,
                    "openai", "gpt-4", {"api_key": "test"},
                )

    # At least one write call should have received discovery context
    assert any("test_concept" in ctx for ctx in write_calls), \
        f"Discovery context not passed to writer. Calls: {write_calls}"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_pipeline.py::test_pipeline_threads_discovery_context -v`
Expected: PASS (if Tasks 1-3 are implemented correctly)

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
cd /Users/sumo/agent-learn
git add backend/tests/test_pipeline.py
git commit -m "test: add integration test for discovery context threading"
```
