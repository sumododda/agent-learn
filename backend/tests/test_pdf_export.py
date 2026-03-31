import uuid

import pytest

from app.pdf_export import MermaidCollector, build_reference_index, convert_markdown_to_typst, generate_course_pdf
from app.schemas import Citation, CourseResponse, SectionFull
from tests.conftest import TEST_USER_UUID


def _sample_course_response() -> CourseResponse:
    section_one_id = uuid.uuid4()
    section_two_id = uuid.uuid4()

    return CourseResponse(
        id=uuid.uuid4(),
        topic="Testing Export",
        instructions=None,
        status="completed",
        ungrounded=False,
        academic_search=None,
        pipeline_status=None,
        sections=[
            SectionFull(
                id=section_one_id,
                position=1,
                title="Introduction",
                summary="Overview",
                content=(
                    "## Introduction\n\n"
                    "Python was created in 1991 [1].\n\n"
                    "### Key Takeaways\n"
                    "- Batteries included\n"
                    "- Dynamic typing\n"
                ),
                citations=[
                    Citation(
                        number=1,
                        claim="Python origin",
                        source_url="https://docs.python.org/3/faq/general.html",
                        source_title="Python FAQ",
                    ),
                    Citation(
                        number=2,
                        claim="Typing",
                        source_url="https://docs.python.org/3/reference/datamodel.html",
                        source_title="Python Data Model",
                    ),
                ],
            ),
            SectionFull(
                id=section_two_id,
                position=2,
                title="Practice",
                summary="Exercises",
                content=(
                    "## Practice\n\n"
                    "Try `print(\"hello\")` and read the docs [1].\n\n"
                    "1. Open a REPL\n"
                    "2. Run the code\n"
                ),
                citations=[
                    Citation(
                        number=1,
                        claim="Docs reuse",
                        source_url="https://docs.python.org/3/faq/general.html",
                        source_title="Python FAQ",
                    ),
                ],
            ),
        ],
    )


async def _create_exportable_course():
    from app.database import get_session
    from app.main import app
    from app.models import Course, Section

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    course = Course(
        topic="Testing Export",
        status="completed",
        user_id=TEST_USER_UUID,
    )
    session.add(course)
    await session.flush()

    session.add_all([
        Section(
            course_id=course.id,
            position=1,
            title="Introduction",
            summary="Overview",
            content="## Introduction\n\nPython was created in 1991 [1].",
            citations=[
                {
                    "number": 1,
                    "claim": "Python origin",
                    "source_url": "https://docs.python.org/3/faq/general.html",
                    "source_title": "Python FAQ",
                }
            ],
        ),
        Section(
            course_id=course.id,
            position=2,
            title="Practice",
            summary="Exercises",
            content="## Practice\n\nTry print(\"hello\").",
            citations=[],
        ),
    ])
    await session.commit()

    try:
        await session_gen.__anext__()
    except StopAsyncIteration:
        pass

    return str(course.id)


async def _create_incomplete_course():
    from app.database import get_session
    from app.main import app
    from app.models import Course, Section

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    course = Course(
        topic="Draft Export",
        status="outline_ready",
        user_id=TEST_USER_UUID,
    )
    session.add(course)
    await session.flush()
    session.add(
        Section(
            course_id=course.id,
            position=1,
            title="Introduction",
            summary="Overview",
            content="## Introduction\n\nDraft content.",
            citations=[],
        )
    )
    await session.commit()

    try:
        await session_gen.__anext__()
    except StopAsyncIteration:
        pass

    return str(course.id)


def test_build_reference_index_deduplicates_by_source_url():
    course = _sample_course_response()

    section_maps, references = build_reference_index(course.sections)

    assert len(references) == 2
    assert section_maps[str(course.sections[0].id)][1] == 1
    assert section_maps[str(course.sections[0].id)][2] == 2
    assert section_maps[str(course.sections[1].id)][1] == 1


def test_convert_markdown_to_typst_renders_supported_elements():
    markdown = (
        "## Intro\n\n"
        "Paragraph with **bold** and *italic* and [link](https://example.com) [1].\n\n"
        "- bullet\n\n"
        "1. ordered\n\n"
        "> quoted\n\n"
        "```python\nprint(1)\n```\n"
    )

    rendered = convert_markdown_to_typst(markdown, {1: 3})

    assert '== #("Intro")' in rendered
    assert '#strong[#("bold")]' in rendered
    assert '#emph[#("italic")]' in rendered
    assert '#link("https://example.com")[#("link")]' in rendered
    assert '#super[#("3")]' in rendered
    assert '- #("bullet")' in rendered
    assert '+ #("ordered")' in rendered
    assert '#quote(block: true)' in rendered
    assert '#raw("print(1)\\n", lang: "python", block: true)' in rendered


def test_convert_markdown_to_typst_renders_mermaid_as_page_fitted_svg():
    markdown = (
        "```mermaid\n"
        "flowchart TD\n"
        '    A["Start"] --> B["Finish"]\n'
        "```\n"
    )
    mermaid = MermaidCollector()

    rendered = convert_markdown_to_typst(markdown, mermaid=mermaid)

    assert mermaid.blocks == ['flowchart TD\n    A["Start"] --> B["Finish"]']
    assert "#align(center)[" in rendered
    assert '#image("mermaid-0.svg", width: 100%, height: 19cm, fit: "contain")' in rendered


def test_generate_course_pdf_returns_pdf_bytes():
    pdf = generate_course_pdf(_sample_course_response())

    assert pdf.startswith(b"%PDF-")


def test_generate_course_pdf_returns_pdf_bytes_for_mermaid(monkeypatch):
    mermaid_course = CourseResponse(
        id=uuid.uuid4(),
        topic="Mermaid Export",
        instructions=None,
        status="completed",
        ungrounded=False,
        academic_search=None,
        pipeline_status=None,
        sections=[
            SectionFull(
                id=uuid.uuid4(),
                position=1,
                title="Diagram",
                summary="Mermaid diagram",
                content=(
                    "## Diagram\n\n"
                    "```mermaid\n"
                    "flowchart TD\n"
                    '    A["Unvetted Shadow AI App"] --> B["IT Discovery & Containment"]\n'
                    '    B --> C["NIST Map: Asset Inventory"]\n'
                    '    C --> D["MITRE ATLAS Threat Modeling"]\n'
                    '    D --> E["Probabilistic SAST / DAST"]\n'
                    '    E --> F["ISO 42001 Lifecycle Audit"]\n'
                    '    F --> G["Provision Sanctioned AI Agent"]\n'
                    "```\n"
                ),
                citations=[],
            )
        ],
    )

    monkeypatch.setattr(
        "app.pdf_export._render_mermaid_svg",
        lambda _source: (
            '<svg xmlns="http://www.w3.org/2000/svg" width="276" height="766" viewBox="0 0 276 766">'
            '<rect width="276" height="766" fill="white" />'
            '<rect x="8" y="8" width="260" height="90" fill="#ece9ff" stroke="#b39ddb" stroke-width="2" />'
            '<text x="30" y="60" font-size="20">Readable diagram</text>'
            "</svg>"
        ),
    )

    pdf = generate_course_pdf(mermaid_course)

    assert pdf.startswith(b"%PDF-")


@pytest.mark.anyio
async def test_export_course_pdf_endpoint_returns_attachment(setup_db, client):
    course_id = await _create_exportable_course()

    response = await client.get(f"/api/courses/{course_id}/export/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="testing-export.pdf"'
    assert response.content.startswith(b"%PDF-")


@pytest.mark.anyio
async def test_export_course_pdf_requires_completed_course(setup_db, client):
    course_id = await _create_incomplete_course()

    response = await client.get(f"/api/courses/{course_id}/export/pdf")

    assert response.status_code == 400
    assert response.json() == {"detail": "Course must be completed before export"}
