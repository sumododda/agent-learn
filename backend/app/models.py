import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, JSON, Index, Text, Integer, UniqueConstraint, LargeBinary, DateTime, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="outline_ready")
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    ungrounded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=datetime.now
    )

    sections: Mapped[list["Section"]] = relationship(
        back_populates="course", order_by="Section.position", cascade="all, delete-orphan"
    )
    research_briefs: Mapped[list["ResearchBrief"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    evidence_cards: Mapped[list["EvidenceCard"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    blackboard: Mapped["Blackboard | None"] = relationship(
        back_populates="course", uselist=False, cascade="all, delete-orphan"
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    learner_progress: Mapped[list["LearnerProgress"]] = relationship(
        cascade="all, delete-orphan"
    )


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=datetime.now
    )

    course: Mapped["Course"] = relationship(back_populates="sections")


class ResearchBrief(Base):
    __tablename__ = "research_briefs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False
    )
    section_position: Mapped[int | None] = mapped_column(
        nullable=True
    )  # null = discovery brief
    questions: Mapped[list] = mapped_column(JSON, default=list)
    source_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    course: Mapped["Course"] = relationship(back_populates="research_briefs")


class EvidenceCard(Base):
    __tablename__ = "evidence_cards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False
    )
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

    course: Mapped["Course"] = relationship(back_populates="evidence_cards")


class Blackboard(Base):
    __tablename__ = "blackboard"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), unique=True, nullable=False
    )
    glossary: Mapped[dict] = mapped_column(JSON, default=dict)
    concept_ownership: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage_map: Mapped[dict] = mapped_column(JSON, default=dict)
    key_points: Mapped[dict] = mapped_column(JSON, default=dict)
    source_log: Mapped[list] = mapped_column(JSON, default=list)
    open_questions: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=datetime.now
    )

    course: Mapped["Course"] = relationship(back_populates="blackboard")


class LearnerProgress(Base):
    __tablename__ = "learner_progress"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), nullable=False)
    current_section: Mapped[int] = mapped_column(Integer, default=0)
    completed_sections: Mapped[list] = mapped_column(JSON, default=list)
    last_accessed_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=datetime.now)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "course_id", name="uq_user_course_progress"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)  # null for user messages
    section_context: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    course: Mapped["Course"] = relationship(back_populates="chat_messages")


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_per_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    credential_hint: Mapped[str] = mapped_column(Text, nullable=False, default="****")
    extra_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UserKeySalt(Base):
    __tablename__ = "user_key_salts"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    salt: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    checkpoint: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSONB().with_variant(JSON, "sqlite"), default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("uq_one_active_job_per_user", "user_id", unique=True, postgresql_where=text("status IN ('pending', 'claimed', 'running')")),
        Index("idx_pipeline_jobs_claimable", "created_at", postgresql_where=text("status = 'pending'")),
        Index("idx_pipeline_jobs_stale", "heartbeat_at", postgresql_where=text("status = 'running'")),
    )
