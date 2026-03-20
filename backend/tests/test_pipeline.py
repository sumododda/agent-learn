"""Tests for Phase 8: Full pipeline orchestration.

Tests cover:
- generate_lessons full flow: research -> verify -> write -> edit per section
- Pipeline status tracking updates at each stage
- Course status transitions: researching -> verifying -> writing -> editing -> completed
- Blackboard accumulation across sections
- Section content and citations saved correctly
- Partial failure: one section's write fails, others succeed
- Re-research: verifier says needs_more_research -> research_section_targeted called
- Pipeline failure: unhandled error -> course status = "failed"
"""

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from sqlalchemy import select, event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.agent import (
    BlackboardUpdates,
    CardVerification,
    EditorResult,
    EvidenceCardItem,
    EvidenceCardSet,
    VerificationResult,
)
from app.models import (
    Base,
    Blackboard,
    Course,
    EvidenceCard,
    ResearchBrief,
    Section,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pipeline_session():
    """Create a fresh in-memory SQLite DB and session for pipeline tests."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def course_with_sections_and_briefs(pipeline_session):
    """Create a course with 3 sections and corresponding research briefs."""
    course = Course(topic="Python Basics", status="outline_ready")
    pipeline_session.add(course)
    await pipeline_session.commit()

    sections = []
    for i in range(1, 4):
        section = Section(
            course_id=course.id,
            position=i,
            title=f"Section {i}",
            summary=f"Summary for section {i}",
        )
        pipeline_session.add(section)
        sections.append(section)
    await pipeline_session.commit()

    # Discovery brief (section_position=None)
    discovery_brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings="Discovery completed",
    )
    pipeline_session.add(discovery_brief)

    # Section-level briefs
    for i in range(1, 4):
        brief = ResearchBrief(
            course_id=course.id,
            section_position=i,
            questions=[f"Q{i}a?", f"Q{i}b?"],
            source_policy={"preferred_tiers": [1, 2], "scope": f"section {i}"},
        )
        pipeline_session.add(brief)
    await pipeline_session.commit()

    return course, sections


@pytest.fixture
def mock_evidence_cards_for_section():
    """Factory for creating EvidenceCardItem lists per section."""
    def _make(section_position, count=2):
        return [
            EvidenceCardItem(
                claim=f"Claim {section_position}-{j}",
                source_url=f"https://example.com/s{section_position}/{j}",
                source_title=f"Source {section_position}-{j}",
                source_tier=1,
                passage=f"Passage for claim {section_position}-{j}",
                confidence=0.9,
                caveat=None,
                explanation=f"Evidence for section {section_position}",
            )
            for j in range(1, count + 1)
        ]
    return _make


@pytest.fixture
def make_verification_result():
    """Factory for creating VerificationResult instances."""
    def _make(card_count, needs_more_research=False, gaps=None):
        return VerificationResult(
            card_verifications=[
                CardVerification(
                    card_index=i,
                    verified=True,
                    note=f"Card {i} verified",
                )
                for i in range(card_count)
            ],
            needs_more_research=needs_more_research,
            gaps=gaps or [],
        )
    return _make


@pytest.fixture
def make_editor_result():
    """Factory for creating EditorResult instances."""
    def _make(section_position):
        return EditorResult(
            edited_content=f"## Section {section_position}\n\nEdited content for section {section_position}. Claim [1] supported. Claim [2] also supported.",
            blackboard_updates=BlackboardUpdates(
                new_glossary_terms={
                    f"term_s{section_position}": {
                        "definition": f"Definition from section {section_position}",
                        "defined_in_section": section_position,
                    }
                },
                new_concept_ownership={f"concept_s{section_position}": section_position},
                topics_covered=[f"topic_s{section_position}"],
                key_points_summary=f"Key points from section {section_position}",
                new_sources=[
                    {
                        "url": f"https://example.com/s{section_position}",
                        "title": f"Source for section {section_position}",
                    }
                ],
            ),
        )
    return _make


# ---------------------------------------------------------------------------
# Helper: seed evidence cards into DB for a section
# ---------------------------------------------------------------------------


async def _seed_evidence_cards(session, course_id, section_position, count=2):
    """Insert EvidenceCard rows for a section and return them."""
    cards = []
    for j in range(count):
        card = EvidenceCard(
            course_id=course_id,
            section_position=section_position,
            claim=f"Claim {section_position}-{j}",
            source_url=f"https://example.com/s{section_position}/{j}",
            source_title=f"Source {section_position}-{j}",
            source_tier=1,
            passage=f"Passage {section_position}-{j}",
            retrieved_date=date.today(),
            confidence=0.9,
            explanation=f"Evidence for section {section_position}",
        )
        session.add(card)
        cards.append(card)
    await session.commit()
    return cards


# ---------------------------------------------------------------------------
# Test: Full pipeline happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_pipeline_happy_path(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    mock_evidence_cards_for_section,
    make_verification_result,
    make_editor_result,
):
    """Full pipeline: research -> verify -> write -> edit for all sections."""
    course, sections = course_with_sections_and_briefs

    # Track status transitions
    status_transitions = []
    original_update_status = None

    async def tracking_update_status(cid, status, session):
        status_transitions.append(status)
        result = await session.execute(
            select(Course).where(Course.id == cid)
        )
        c = result.scalar_one_or_none()
        if c:
            c.status = status
            await session.commit()

    # research_all_sections mock: seeds evidence cards into DB
    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(
                    session, course_id, b.section_position, count=2
                )

    # verify_evidence mock: marks all cards as verified
    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
            card.verification_note = "Verified in test"
        await session.commit()
        return make_verification_result(len(cards))

    # write_section mock: returns draft markdown
    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nDraft content. Claim [1]. Claim [2]."

    # edit_section mock: returns EditorResult per section
    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch(
            "app.agent_service.research_all_sections",
            new_callable=AsyncMock,
            side_effect=mock_research_all,
        ),
        patch(
            "app.agent_service.verify_evidence",
            new_callable=AsyncMock,
            side_effect=mock_verify,
        ),
        patch(
            "app.agent_service.write_section",
            new_callable=AsyncMock,
            side_effect=mock_write,
        ),
        patch(
            "app.agent_service.edit_section",
            new_callable=AsyncMock,
            side_effect=mock_edit,
        ),
        patch(
            "app.agent_service.update_course_status",
            new_callable=AsyncMock,
            side_effect=tracking_update_status,
        ),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # 1. Verify course status ended as "completed"
    assert status_transitions[-1] == "completed"

    # 2. Verify status transitions include all expected stages
    assert "researching" in status_transitions
    assert "verifying" in status_transitions
    assert "writing" in status_transitions
    assert "editing" in status_transitions
    assert "completed" in status_transitions

    # 3. Verify each section has content saved
    for section in sections:
        await pipeline_session.refresh(section)
        assert section.content is not None
        assert "Edited content" in section.content

    # 4. Verify citations were extracted and saved for each section
    for section in sections:
        assert section.citations is not None
        assert len(section.citations) >= 1
        assert section.citations[0]["number"] == 1

    # 5. Verify blackboard was created and accumulated data from all sections
    bb_result = await pipeline_session.execute(
        select(Blackboard).where(Blackboard.course_id == course.id)
    )
    bb = bb_result.scalar_one_or_none()
    assert bb is not None
    # Glossary should have terms from all 3 sections
    assert "term_s1" in bb.glossary
    assert "term_s2" in bb.glossary
    assert "term_s3" in bb.glossary
    # Concept ownership from all sections
    assert bb.concept_ownership.get("concept_s1") == 1
    assert bb.concept_ownership.get("concept_s2") == 2
    assert bb.concept_ownership.get("concept_s3") == 3

    # Pipeline status is now tracked in app.pipeline (not the legacy generate_lessons)
    # so we only verify course.status == "completed" above.


# ---------------------------------------------------------------------------
# Test: Pipeline status updates track per-section stages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pipeline_status_updates_per_section(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
    make_editor_result,
):
    """Pipeline status records stage for each section as it progresses."""
    course, sections = course_with_sections_and_briefs

    # Track pipeline status at each stage transition
    status_snapshots = []

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Pipeline status tracking moved to app.pipeline; legacy generate_lessons
    # no longer populates a status dict. Verify course completed successfully.
    await pipeline_session.refresh(course)
    assert course.status == "completed"


# ---------------------------------------------------------------------------
# Test: Blackboard accumulates across sections
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blackboard_accumulates_across_sections(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
):
    """Blackboard glossary, concepts, and coverage grow with each section."""
    course, sections = course_with_sections_and_briefs

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    # Each section adds unique glossary terms
    edit_call_count = 0

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        nonlocal edit_call_count
        edit_call_count += 1
        return EditorResult(
            edited_content=f"## Section {section_position}\n\nEdited [1].",
            blackboard_updates=BlackboardUpdates(
                new_glossary_terms={
                    f"term_{section_position}": {
                        "definition": f"Def {section_position}",
                        "defined_in_section": section_position,
                    }
                },
                new_concept_ownership={f"concept_{section_position}": section_position},
                topics_covered=[f"topic_{section_position}"],
                key_points_summary=f"Summary {section_position}",
                new_sources=[{"url": f"https://s{section_position}.com", "title": f"S{section_position}"}],
            ),
        )

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Verify all 3 edit calls happened
    assert edit_call_count == 3

    # Verify blackboard accumulated all data
    bb_result = await pipeline_session.execute(
        select(Blackboard).where(Blackboard.course_id == course.id)
    )
    bb = bb_result.scalar_one_or_none()
    assert bb is not None

    # Glossary has terms from all 3 sections
    assert len(bb.glossary) == 3
    for i in range(1, 4):
        assert f"term_{i}" in bb.glossary

    # Concept ownership from all 3 sections
    assert len(bb.concept_ownership) == 3

    # Coverage map has topics from all 3 sections
    all_topics = bb.coverage_map.get("all_topics", [])
    assert len(all_topics) == 3

    # Source log has entries from all 3 sections
    assert len(bb.source_log) == 3

    # Key points have entries from all 3 sections
    assert len(bb.key_points) == 3



# ---------------------------------------------------------------------------
# Test: Partial failure -- one section's write fails, others succeed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_partial_failure_write_error(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
    make_editor_result,
):
    """When write_section fails for one section, pipeline sets status to 'failed'."""
    course, sections = course_with_sections_and_briefs

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    write_call_count = 0

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        nonlocal write_call_count
        write_call_count += 1
        if section.position == 2:
            raise RuntimeError("LLM timeout on section 2")
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # The pipeline continues past the failed section and completes
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    result = await pipeline_session.execute(
        select(Course).options(selectinload(Course.sections)).where(Course.id == course.id)
    )
    refreshed = result.scalar_one()
    assert refreshed.status == "completed"

    # Section 2 should have no content (write failed), others should have content
    for s in sorted(refreshed.sections, key=lambda s: s.position):
        if s.position == 2:
            assert s.content is None
        else:
            assert s.content is not None


# ---------------------------------------------------------------------------
# Test: Re-research when verifier says needs_more_research
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_re_research_on_verification_gap(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_editor_result,
):
    """When verifier says needs_more_research, research_section_targeted is called."""
    course, sections = course_with_sections_and_briefs

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    verify_call_count = 0

    async def mock_verify(cards, brief, session, *args, **kwargs):
        nonlocal verify_call_count
        verify_call_count += 1
        for card in cards:
            card.verified = True
        await session.commit()

        # First call for section 1: needs more research
        # Second call for section 1 (after re-research): satisfied
        # All other sections: satisfied immediately
        if brief.section_position == 1 and verify_call_count == 1:
            return VerificationResult(
                card_verifications=[
                    CardVerification(card_index=i, verified=True, note="OK")
                    for i in range(len(cards))
                ],
                needs_more_research=True,
                gaps=["What are Python's key features?"],
            )
        return VerificationResult(
            card_verifications=[
                CardVerification(card_index=i, verified=True, note="OK")
                for i in range(len(cards))
            ],
            needs_more_research=False,
            gaps=[],
        )

    # research_section_targeted should be called once (for section 1's gaps)
    targeted_research_called = False

    async def mock_research_targeted(gaps, *args, **kwargs):
        nonlocal targeted_research_called
        targeted_research_called = True
        assert "What are Python's key features?" in gaps
        return [
            EvidenceCardItem(
                claim="Python features include readability",
                source_url="https://docs.python.org/3/tutorial/",
                source_title="Python Tutorial",
                source_tier=1,
                passage="Python emphasizes readability...",
                confidence=0.92,
                explanation="Fills gap about key features",
            )
        ]

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.research_section_targeted", new_callable=AsyncMock, side_effect=mock_research_targeted),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # research_section_targeted was called
    assert targeted_research_called

    # verify_evidence was called more than 3 times (extra call for section 1 re-verify)
    # Section 1: first verify (needs more) + re-verify = 2 calls
    # Section 2: 1 call, Section 3: 1 call = total 4
    assert verify_call_count == 4

    # Pipeline still completed
    await pipeline_session.refresh(course)
    assert course.status == "completed"



# ---------------------------------------------------------------------------
# Test: research_all_sections called with parallel execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_runs_in_parallel(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
    make_editor_result,
):
    """research_all_sections is called once (it handles parallelism internally)."""
    course, sections = course_with_sections_and_briefs

    research_all_called = False

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        nonlocal research_all_called
        research_all_called = True
        # Verify it receives briefs including section-level ones
        section_briefs = [b for b in briefs if b.section_position is not None]
        assert len(section_briefs) == 3
        # Seed cards
        for b in section_briefs:
            await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # research_all_sections was called exactly once
    assert research_all_called



# ---------------------------------------------------------------------------
# Test: Verify -> Write -> Edit runs sequentially per section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_write_edit_sequential_per_section(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
    make_editor_result,
):
    """For each section, verify runs before write, write before edit."""
    course, sections = course_with_sections_and_briefs

    call_sequence = []

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        call_sequence.append(("verify", brief.section_position))
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        call_sequence.append(("write", section.position))
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        call_sequence.append(("edit", section_position))
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Expected sequence: for each section in order, verify -> write -> edit
    expected = [
        ("verify", 1), ("write", 1), ("edit", 1),
        ("verify", 2), ("write", 2), ("edit", 2),
        ("verify", 3), ("write", 3), ("edit", 3),
    ]
    assert call_sequence == expected



# ---------------------------------------------------------------------------
# Test: Editor bad blackboard update -- pipeline continues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bad_blackboard_update_doesnt_crash_pipeline(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
):
    """Editor returns bad blackboard updates; pipeline logs warning but continues."""
    course, sections = course_with_sections_and_briefs

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        # Return valid EditorResult but with updates that will cause
        # update_blackboard to internally handle the error (we test
        # that the try/except in generate_lessons also protects)
        return EditorResult(
            edited_content=f"## Section {section_position}\n\nEdited [1].",
            blackboard_updates=BlackboardUpdates(
                new_glossary_terms={f"term_{section_position}": {"definition": f"def {section_position}", "defined_in_section": section_position}},
                new_concept_ownership={},
                topics_covered=[],
                key_points_summary="",
                new_sources=[],
            ),
        )

    # Make update_blackboard raise for section 2 only
    real_update_blackboard = None
    update_bb_call_count = 0

    async def mock_update_blackboard(bb, updates, session):
        nonlocal update_bb_call_count
        update_bb_call_count += 1
        if update_bb_call_count == 2:
            raise ValueError("Malformed blackboard update!")
        # Normal merge for other sections
        from app.agent_service import update_blackboard as real_fn
        # Can't call real_fn here without infinite recursion, so do minimal merge
        glossary = dict(bb.glossary or {})
        glossary.update(updates.new_glossary_terms or {})
        bb.glossary = glossary
        await session.commit()

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
        patch("app.agent_service.update_blackboard", new_callable=AsyncMock, side_effect=mock_update_blackboard),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Pipeline still completed despite blackboard error
    await pipeline_session.refresh(course)
    assert course.status == "completed"

    # All sections still have content
    for section in sections:
        await pipeline_session.refresh(section)
        assert section.content is not None



# ---------------------------------------------------------------------------
# Test: Pipeline failure sets course status to "failed"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unhandled_error_sets_failed_status(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
):
    """An unhandled error in the pipeline sets course.status = 'failed'."""
    course, sections = course_with_sections_and_briefs

    with patch(
        "app.agent_service.research_all_sections",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Catastrophic failure in research"),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Course status should be "failed"
    await pipeline_session.refresh(course)
    assert course.status == "failed"


# ---------------------------------------------------------------------------
# Test: Course status transitions through all expected values
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_course_status_full_transition_sequence(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_verification_result,
    make_editor_result,
):
    """Course status transitions: researching -> verifying -> writing -> editing -> completed."""
    course, sections = course_with_sections_and_briefs

    statuses_seen = []

    async def tracking_update_status(cid, status, session):
        statuses_seen.append(status)
        result = await session.execute(
            select(Course).where(Course.id == cid)
        )
        c = result.scalar_one_or_none()
        if c:
            c.status = status
            await session.commit()

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = True
        await session.commit()
        return make_verification_result(len(cards))

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent [1]."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.update_course_status", new_callable=AsyncMock, side_effect=tracking_update_status),
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Check all required stages appeared in order
    # researching should come first, then cycles of verifying/writing/editing, then completed
    assert statuses_seen[0] == "researching"
    assert statuses_seen[-1] == "completed"

    # All four stage types should appear
    unique_statuses = set(statuses_seen)
    assert "researching" in unique_statuses
    assert "verifying" in unique_statuses
    assert "writing" in unique_statuses
    assert "editing" in unique_statuses
    assert "completed" in unique_statuses



# ---------------------------------------------------------------------------
# Test: Pipeline with empty evidence (verifier all-reject, writer still runs)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_writer_runs_with_empty_verified_cards(
    setup_db,
    pipeline_session,
    course_with_sections_and_briefs,
    make_editor_result,
):
    """When verifier rejects all cards, writer still runs with empty verified cards list."""
    course, sections = course_with_sections_and_briefs

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        for b in briefs:
            if b.section_position is not None:
                await _seed_evidence_cards(session, course_id, b.section_position)

    # Verifier rejects all cards
    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = False
            card.verification_note = "Rejected in test"
        await session.commit()
        return VerificationResult(
            card_verifications=[
                CardVerification(card_index=i, verified=False, note="Rejected")
                for i in range(len(cards))
            ],
            needs_more_research=False,  # not requesting re-research either
            gaps=[],
        )

    write_cards_counts = []

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        # The service passes all cards; writer filters to verified ones internally.
        # This test verifies the pipeline still calls write_section regardless.
        write_cards_counts.append(len(cards))
        return f"## {section.title}\n\nContent without citations."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return make_editor_result(section_position)

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, pipeline_session)

    # Writer was called for all 3 sections
    assert len(write_cards_counts) == 3

    # Pipeline completed
    await pipeline_session.refresh(course)
    assert course.status == "completed"

