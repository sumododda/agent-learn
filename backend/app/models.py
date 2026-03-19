import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, JSON, Text, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="outline_ready")
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ungrounded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=datetime.now
    )

    sections: Mapped[list["Section"]] = relationship(
        back_populates="course", order_by="Section.position"
    )
    research_briefs: Mapped[list["ResearchBrief"]] = relationship(
        back_populates="course"
    )
    evidence_cards: Mapped[list["EvidenceCard"]] = relationship(
        back_populates="course"
    )
    blackboard: Mapped["Blackboard | None"] = relationship(
        back_populates="course", uselist=False
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
