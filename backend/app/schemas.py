from uuid import UUID

from pydantic import BaseModel


class CourseCreate(BaseModel):
    topic: str
    instructions: str | None = None


class SectionOutline(BaseModel):
    position: int
    title: str
    summary: str


class SectionFull(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    position: int
    title: str
    summary: str
    content: str | None = None


class CourseResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    topic: str
    instructions: str | None
    status: str
    sections: list[SectionFull]


class GenerateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    status: str
    sections: list[SectionFull]
