from datetime import date, datetime
from uuid import UUID

import re

from pydantic import BaseModel, EmailStr, Field, field_validator


class AcademicSearchOptions(BaseModel):
    enabled: bool = False
    year_range: str = Field(default="5y", pattern=r"^(5y|10y|20y|all)$")
    min_citations: int = Field(default=0, ge=0)
    open_access_only: bool = False


class CourseCreate(BaseModel):
    topic: str = Field(max_length=500)
    instructions: str | None = Field(default=None, max_length=5000)
    academic_search: AcademicSearchOptions | None = None


class SectionComment(BaseModel):
    position: int
    comment: str = Field(max_length=5000)


class RegenerateRequest(BaseModel):
    overall_comment: str | None = Field(default=None, max_length=5000)
    section_comments: list[SectionComment] = Field(default=[], max_length=50)


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
    academic_search: dict | None = None
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
    is_academic: bool = False
    academic_authors: str | None = None
    academic_year: int | None = None
    academic_venue: str | None = None
    academic_doi: str | None = None
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
    current_section: int | None = Field(default=None, ge=0, le=200)
    completed_section: int | None = Field(default=None, ge=0, le=200)


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
    section_context: int = Field(ge=0, le=200)


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


def _validate_password_strength(password: str) -> str:
    """Require at least one uppercase, one lowercase, one digit."""
    if not re.search(r'[A-Z]', password):
        raise ValueError('Password must contain at least one uppercase letter')
    if not re.search(r'[a-z]', password):
        raise ValueError('Password must contain at least one lowercase letter')
    if not re.search(r'[0-9]', password):
        raise ValueError('Password must contain at least one digit')
    return password


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    turnstile_token: str

    _check_password_strength = field_validator('password')(_validate_password_strength)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordConfirmRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)

    _check_password_strength = field_validator('new_password')(_validate_password_strength)


class ForgotPasswordResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Provider schemas (Phase 2: Multi-provider)
# ---------------------------------------------------------------------------


def _validate_credential_values(v: dict | None) -> dict | None:
    """Reject credential values longer than 10,000 characters."""
    if v:
        for key, val in v.items():
            if isinstance(val, str) and len(val) > 10000:
                raise ValueError(f'Credential value for "{key}" exceeds maximum length')
    return v


class ProviderSaveRequest(BaseModel):
    provider: str = Field(max_length=100)
    credentials: dict = Field(max_length=10)
    extra_fields: dict = Field(default={}, max_length=10)
    password: str | None = None

    _check_cred_values = field_validator('credentials')(_validate_credential_values)


class ProviderUpdateRequest(BaseModel):
    credentials: dict | None = Field(default=None, max_length=10)
    extra_fields: dict | None = Field(default=None, max_length=10)
    password: str | None = None

    _check_cred_values = field_validator('credentials')(_validate_credential_values)


class ProviderTestRequest(BaseModel):
    credentials: dict = Field(max_length=10)
    extra_fields: dict = Field(default={}, max_length=10)


class ProviderConfigResponse(BaseModel):
    provider: str
    name: str
    credential_hint: str
    extra_fields: dict
    is_default: bool


class ProviderDefaultRequest(BaseModel):
    provider: str


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    _check_password_strength = field_validator('new_password')(_validate_password_strength)
