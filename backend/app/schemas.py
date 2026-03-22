from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CourseCreate(BaseModel):
    topic: str = Field(max_length=500)
    instructions: str | None = Field(default=None, max_length=5000)


class SectionComment(BaseModel):
    position: int
    comment: str = Field(max_length=5000)


class RegenerateRequest(BaseModel):
    overall_comment: str | None = Field(default=None, max_length=5000)
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


class PipelineStatusResponse(BaseModel):
    stage: str
    section: int = 0
    total: int = 0
    error: str | None = None


class PipelineJobResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    course_id: UUID
    status: str
    checkpoint: int
    error: str | None = None
    attempts: int
    created_at: datetime


class ResumeResponse(BaseModel):
    job_id: UUID
    checkpoint: int


class CourseResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    topic: str
    instructions: str | None
    status: str
    ungrounded: bool = False
    sections: list[SectionFull]
    pipeline_status: PipelineStatusResponse | None = None


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


# ---------------------------------------------------------------------------
# Learner progress schemas (Phase 6, Milestone 3)
# ---------------------------------------------------------------------------


class ProgressUpdateRequest(BaseModel):
    """Request body for POST /api/courses/{id}/progress."""
    current_section: int | None = None
    completed_section: int | None = None


class ProgressResponse(BaseModel):
    """Progress data returned from progress endpoints."""
    model_config = {"from_attributes": True}

    current_section: int
    completed_sections: list[int]
    last_accessed_at: datetime


class CourseWithProgressResponse(BaseModel):
    """Course with optional progress info for the library page."""
    model_config = {"from_attributes": True}

    id: UUID
    topic: str
    instructions: str | None
    status: str
    ungrounded: bool = False
    sections: list[SectionFull]
    progress: ProgressResponse | None = None


# ---------------------------------------------------------------------------
# Chat schemas (Phase 2, Milestone 4)
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(max_length=10000)
    model: str = Field(max_length=200)
    section_context: int


class ChatMessageResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    role: str
    content: str
    model: str | None
    section_context: int
    created_at: datetime


class ChatModelInfo(BaseModel):
    id: str
    name: str
    context_length: int
    pricing_prompt: str
    pricing_completion: str


# ---------------------------------------------------------------------------
# Auth schemas (Phase 1: Backend auth replacement)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    turnstile_token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    user_id: str
    provider_keys_loaded: bool = True


class RegisterResponse(BaseModel):
    message: str
    email: str


class OtpVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class OtpResendRequest(BaseModel):
    email: EmailStr


class OtpResendResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Provider schemas (Phase 2: Multi-provider)
# ---------------------------------------------------------------------------


class ProviderSaveRequest(BaseModel):
    provider: str
    credentials: dict
    extra_fields: dict = {}
    password: str | None = None


class ProviderUpdateRequest(BaseModel):
    credentials: dict | None = None
    extra_fields: dict | None = None
    password: str | None = None


class ProviderTestRequest(BaseModel):
    credentials: dict
    extra_fields: dict = {}


class ProviderConfigResponse(BaseModel):
    provider: str
    name: str
    credential_hint: str
    extra_fields: dict
    is_default: bool


class ProviderDefaultRequest(BaseModel):
    provider: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
