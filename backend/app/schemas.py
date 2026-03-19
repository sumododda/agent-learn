from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class CourseCreate(BaseModel):
    topic: str
    instructions: str | None = None


class SectionComment(BaseModel):
    position: int
    comment: str


class RegenerateRequest(BaseModel):
    overall_comment: str | None = None
    section_comments: list[SectionComment] = []


class SectionOutline(BaseModel):
    position: int
    title: str
    summary: str


class Citation(BaseModel):
    number: int
    claim: str
    source_url: str
    source_title: str


class SectionFull(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    position: int
    title: str
    summary: str
    content: str | None = None
    citations: list[Citation] | None = None


class CourseResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    topic: str
    instructions: str | None
    status: str
    ungrounded: bool = False
    sections: list[SectionFull]


class GenerateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    status: str
    sections: list[SectionFull]


class EvidenceCardResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    course_id: UUID
    section_position: int
    claim: str
    source_url: str
    source_title: str
    source_tier: int
    passage: str
    retrieved_date: date
    confidence: float
    caveat: str | None = None
    explanation: str
    verified: bool = False
    verification_note: str | None = None
    created_at: datetime


class ResearchBriefResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    course_id: UUID
    section_position: int | None = None
    questions: list = []
    source_policy: dict = {}
    findings: str | None = None
    created_at: datetime


class BlackboardResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    course_id: UUID
    glossary: dict = {}
    concept_ownership: dict = {}
    coverage_map: dict = {}
    key_points: dict = {}
    source_log: list = []
    open_questions: list = []
    updated_at: datetime


class SectionPipelineStatus(BaseModel):
    position: int
    stage: str


class PipelineStatus(BaseModel):
    course_id: str
    stage: str
    current_section: int | None = None
    sections: dict[int, str] = {}


# ---------------------------------------------------------------------------
# Internal API endpoint schemas (Phase 2, Milestone 3)
# ---------------------------------------------------------------------------


class InternalCourseRequest(BaseModel):
    """Request body for endpoints that only need a course_id."""
    course_id: str


class InternalSectionRequest(BaseModel):
    """Request body for endpoints that need course_id + section_position."""
    course_id: str
    section_position: int


class InternalSectionInfo(BaseModel):
    """A section returned from discover-and-plan."""
    id: str
    position: int
    title: str
    summary: str


class InternalResearchBriefInfo(BaseModel):
    """A research brief returned from discover-and-plan."""
    id: str
    section_position: int | None = None
    questions: list = []
    source_policy: dict = {}


class DiscoverAndPlanResponse(BaseModel):
    """Response from POST /api/internal/discover-and-plan."""
    sections: list[InternalSectionInfo]
    research_briefs: list[InternalResearchBriefInfo]
    ungrounded: bool = False


class InternalEvidenceCardInfo(BaseModel):
    """An evidence card returned from research-section."""
    id: str
    section_position: int
    claim: str
    source_url: str
    source_title: str
    source_tier: int
    passage: str
    retrieved_date: str
    confidence: float
    caveat: str | None = None
    explanation: str
    verified: bool = False


class ResearchSectionResponse(BaseModel):
    """Response from POST /api/internal/research-section."""
    evidence_cards: list[InternalEvidenceCardInfo]


class VerificationResultInfo(BaseModel):
    """Verification result details."""
    cards_verified: int
    cards_total: int
    needs_more_research: bool
    gaps: list[str] = []


class VerifySectionResponse(BaseModel):
    """Response from POST /api/internal/verify-section."""
    verification_result: VerificationResultInfo


class WriteSectionResponse(BaseModel):
    """Response from POST /api/internal/write-section."""
    content: str
    citations: list[dict] = []


class BlackboardUpdatesInfo(BaseModel):
    """Blackboard updates returned from edit-section."""
    new_glossary_terms: dict = {}
    new_concept_ownership: dict = {}
    topics_covered: list[str] = []
    key_points_summary: str = ""
    new_sources: list[dict] = []


class EditSectionResponse(BaseModel):
    """Response from POST /api/internal/edit-section."""
    edited_content: str
    blackboard_updates: BlackboardUpdatesInfo
