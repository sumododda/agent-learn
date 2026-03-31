from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from app.schemas import CourseResponse, SectionFull

try:
    import typst
except ModuleNotFoundError:  # pragma: no cover - exercised in runtime environments without the dependency
    typst = None


_MARKDOWN = MarkdownIt("commonmark")
_LEADING_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}[^\n]*(?:\r?\n){1,2}")
_CITATION_RE = re.compile(r"\[(\d+)\]")
_REMOTE_ASSET_RE = re.compile(r"^[a-z]+://", re.IGNORECASE)
_TEMPLATE_PATH = Path(__file__).with_name("templates") / "course.typ"


@dataclass(frozen=True)
class ReferenceEntry:
    number: int
    source_title: str
    source_url: str


def sanitize_pdf_filename(topic: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "-", topic.strip().lower()).strip("-")
    return sanitized or "course"


def build_reference_index(
    sections: list[SectionFull],
) -> tuple[dict[str, dict[int, int]], list[ReferenceEntry]]:
    section_maps: dict[str, dict[int, int]] = {}
    reference_numbers: dict[str, int] = {}
    references: list[ReferenceEntry] = []

    for section in sorted(sections, key=lambda item: item.position):
        local_map: dict[int, int] = {}
        for citation in section.citations or []:
            source_key = citation.source_url or f"{citation.source_title}:{citation.number}"
            global_number = reference_numbers.get(source_key)
            if global_number is None:
                global_number = len(references) + 1
                reference_numbers[source_key] = global_number
                references.append(
                    ReferenceEntry(
                        number=global_number,
                        source_title=citation.source_title or citation.source_url,
                        source_url=citation.source_url,
                    )
                )
            local_map[citation.number] = global_number
        section_maps[str(section.id)] = local_map

    return section_maps, references


def render_course_typst(course: CourseResponse) -> str:
    section_maps, references = build_reference_index(course.sections)
    generated_at = _format_cover_date(datetime.now())

    parts = [
        '#import "course.typ": render_cover, render_references, render_section',
        f"#render_cover({ _typst_string(course.topic) }, { _typst_string(generated_at) })",
        "#outline(title: [Contents])",
    ]

    for section in sorted(course.sections, key=lambda item: item.position):
        markdown = strip_leading_heading(section.content or "").strip()
        if not markdown:
            continue

        body = convert_markdown_to_typst(markdown, section_maps.get(str(section.id), {}))
        if not body.strip():
            continue

        parts.append(
            f"#render_section({_typst_string(section.title)})[\n{_indent(body)}\n]"
        )

    if references:
        parts.append(render_references_typst(references))

    return "\n\n".join(parts) + "\n"


def render_references_typst(references: list[ReferenceEntry]) -> str:
    items: list[str] = []
    for reference in references:
        title = reference.source_title.strip() or reference.source_url
        title = title if title.endswith((".", "!", "?")) else f"{title}."

        if reference.source_url:
            item_body = (
                f'#strong[{_render_text_literal(f"[{reference.number}]")}] '
                f'{_render_text_literal(f"{title} ")}'
                f'#link({_typst_string(reference.source_url)})'
                f'[{_render_text_literal(reference.source_url)}]'
            )
        else:
            item_body = (
                f'#strong[{_render_text_literal(f"[{reference.number}]")}] '
                f'{_render_text_literal(title)}'
            )

        items.append(f"+ [{item_body}]")

    return "#render_references[\n" + _indent("\n".join(items)) + "\n]"


def generate_course_pdf(course: CourseResponse) -> bytes:
    if typst is None:
        raise RuntimeError("Typst dependency is not installed")

    main_source = render_course_typst(course)
    template_source = _TEMPLATE_PATH.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="course-export-") as tmpdir:
        root = Path(tmpdir)
        (root / "main.typ").write_text(main_source, encoding="utf-8")
        (root / "course.typ").write_text(template_source, encoding="utf-8")
        return typst.compile(root / "main.typ")


def strip_leading_heading(markdown: str) -> str:
    return _LEADING_HEADING_RE.sub("", markdown, count=1)


def convert_markdown_to_typst(markdown: str, citation_map: dict[int, int] | None = None) -> str:
    root = SyntaxTreeNode(_MARKDOWN.parse(markdown))
    blocks = [_render_block(node, citation_map or {}) for node in root.children]
    return "\n\n".join(block for block in blocks if block.strip())


def _render_block(node: SyntaxTreeNode, citation_map: dict[int, int]) -> str:
    if node.type == "heading":
        level = max(1, int(node.tag.removeprefix("h") or "1"))
        return f"{'=' * level} {_render_inline_nodes(node.children, citation_map)}"

    if node.type == "paragraph":
        return _render_inline_nodes(node.children, citation_map)

    if node.type == "bullet_list":
        return _render_list(node.children, "- ", citation_map)

    if node.type == "ordered_list":
        return _render_list(node.children, "+ ", citation_map)

    if node.type == "blockquote":
        inner = _render_blocks(node.children, citation_map)
        return f"#quote(block: true)[\n{_indent(inner)}\n]"

    if node.type in {"fence", "code_block"}:
        return _render_code_block(node.content, node.info)

    if node.type == "html_block":
        return f"#raw({_typst_string(node.content)}, block: true)"

    return _render_blocks(node.children, citation_map)


def _render_blocks(nodes: list[SyntaxTreeNode], citation_map: dict[int, int]) -> str:
    blocks = [_render_block(node, citation_map) for node in nodes]
    return "\n\n".join(block for block in blocks if block.strip())


def _render_list(
    items: list[SyntaxTreeNode],
    marker: str,
    citation_map: dict[int, int],
) -> str:
    rendered: list[str] = []
    for item in items:
        body = _render_blocks(item.children, citation_map).strip()
        if not body:
            continue
        lines = body.splitlines()
        first = marker + lines[0]
        rest = [f"  {line}" if line else "" for line in lines[1:]]
        rendered.append("\n".join([first, *rest]))
    return "\n".join(rendered)


def _render_inline_nodes(nodes: list[SyntaxTreeNode], citation_map: dict[int, int]) -> str:
    return "".join(_render_inline_node(node, citation_map) for node in nodes)


def _render_inline_node(node: SyntaxTreeNode, citation_map: dict[int, int]) -> str:
    if node.type == "inline":
        return _render_inline_nodes(node.children, citation_map)

    if node.type == "text":
        return _render_text_with_citations(node.content, citation_map)

    if node.type == "softbreak":
        return "\n"

    if node.type == "hardbreak":
        return "#linebreak()"

    if node.type == "strong":
        return f"#strong[{_render_inline_nodes(node.children, citation_map)}]"

    if node.type == "em":
        return f"#emph[{_render_inline_nodes(node.children, citation_map)}]"

    if node.type == "link":
        href = node.attrs.get("href", "")
        return f"#link({_typst_string(href)})[{_render_inline_nodes(node.children, citation_map)}]"

    if node.type == "image":
        src = node.attrs.get("src", "")
        alt = node.content or _extract_plain_text(node.children) or src
        if _REMOTE_ASSET_RE.match(src):
            label = alt if alt and alt != src else "Image"
            return (
                f"#link({_typst_string(src)})"
                f'[{_render_text_literal(f"{label}: {src}" if label == "Image" else label)}]'
            )
        return f"#image({_typst_string(src)}, alt: {_typst_string(alt)})"

    if node.type == "code_inline":
        return f"#raw({_typst_string(node.content)})"

    if node.children:
        return _render_inline_nodes(node.children, citation_map)

    if node.content:
        return _render_text_with_citations(node.content, citation_map)

    return ""


def _render_code_block(content: str, info: str | None) -> str:
    language = (info or "").strip().split(maxsplit=1)[0]
    if language == "mermaid":
        return _render_mermaid_placeholder(content)
    if language:
        return f"#raw({_typst_string(content)}, lang: {_typst_string(language)}, block: true)"
    return f"#raw({_typst_string(content)}, block: true)"


def _render_mermaid_placeholder(content: str) -> str:
    """Render a mermaid diagram as a styled box with the raw definition for PDF export."""
    return (
        "#block(width: 100%, inset: 12pt, stroke: 0.5pt + luma(180), radius: 4pt, fill: luma(245))[\n"
        f"  #text(weight: \"bold\", size: 9pt)[Diagram]\n"
        f"  #v(4pt)\n"
        f"  #raw({_typst_string(content.strip())}, block: true)\n"
        "]"
    )


def _render_text_with_citations(text: str, citation_map: dict[int, int]) -> str:
    pieces: list[str] = []
    cursor = 0

    for match in _CITATION_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            pieces.append(_render_text_literal(text[cursor:start]))

        local_number = int(match.group(1))
        global_number = citation_map.get(local_number, local_number)
        pieces.append(f"#super[{_render_text_literal(str(global_number))}]")
        cursor = end

    if cursor < len(text):
        pieces.append(_render_text_literal(text[cursor:]))

    return "".join(pieces)


def _render_text_literal(text: str) -> str:
    if not text:
        return ""
    return f"#({_typst_string(text)})"


def _extract_plain_text(nodes: list[SyntaxTreeNode]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.content:
            parts.append(node.content)
        if node.children:
            parts.append(_extract_plain_text(node.children))
    return "".join(parts)


def _format_cover_date(value: datetime) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else "" for line in text.splitlines())


def _typst_string(value: str) -> str:
    return json.dumps(value)
