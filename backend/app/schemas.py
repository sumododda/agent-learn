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
