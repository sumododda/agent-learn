"""PDF download, Docling parsing, section normalization, and paper reader orchestration."""
import asyncio
import logging
import re
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section name normalization
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: dict[str, list[str]] = {
    "methods": ["method", "approach", "experimental setup", "experiment", "implementation", "design", "methodology"],
    "results": ["result", "finding", "evaluation", "performance"],
    "discussion": ["discussion", "analysis", "interpretation"],
    "conclusion": ["conclusion", "summary", "concluding", "future work"],
    "introduction": ["introduction", "background", "overview", "motivation"],
    "related_work": ["related work", "literature review", "prior work", "state of the art"],
}


def normalize_section_name(heading: str) -> str:
    """Map a paper section heading to a canonical name."""
    cleaned = re.sub(r"^[\dIVXivx]+[\.\)]\s*", "", heading).strip()
    lower = cleaned.lower()
    for canonical, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return canonical
    return "other"


# ---------------------------------------------------------------------------
# PDF Download
# ---------------------------------------------------------------------------

async def download_pdf(url: str) -> str | None:
    """Download a PDF from a URL to a temp file. Returns path or None on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=30.0)
            resp.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(resp.content)
        tmp.close()
        logger.info("[paper_reader] Downloaded PDF (%d bytes) to %s", len(resp.content), tmp.name)
        return tmp.name
    except Exception as e:
        logger.warning("[paper_reader] Failed to download PDF from %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Docling Parsing
# ---------------------------------------------------------------------------

async def parse_pdf_sections(pdf_path: str) -> list[dict[str, str]]:
    """Parse a PDF into labeled sections using Docling.
    Returns list of {heading, text, canonical} dicts. Empty list on failure.
    """
    try:
        def _parse():
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(pdf_path)
            doc = result.document

            sections = []
            current_heading = "Untitled"
            current_text_parts: list[str] = []

            for item in doc.iterate_items():
                label = item[1].label if hasattr(item[1], "label") else ""
                text = item[1].text if hasattr(item[1], "text") else ""

                if label in ("section_header", "title") and text.strip():
                    if current_text_parts:
                        sections.append({
                            "heading": current_heading,
                            "text": "\n".join(current_text_parts),
                            "canonical": normalize_section_name(current_heading),
                        })
                    current_heading = text.strip()
                    current_text_parts = []
                elif text.strip():
                    current_text_parts.append(text.strip())

            if current_text_parts:
                sections.append({
                    "heading": current_heading,
                    "text": "\n".join(current_text_parts),
                    "canonical": normalize_section_name(current_heading),
                })

            return sections

        return await asyncio.to_thread(_parse)
    except Exception as e:
        logger.warning("[paper_reader] Docling parsing failed for %s: %s", pdf_path, e)
        return []


# ---------------------------------------------------------------------------
# Section Selection
# ---------------------------------------------------------------------------

DEFAULT_SECTIONS = {"results", "discussion", "conclusion"}
SKIP_SECTIONS = {"introduction", "related_work"}


def select_sections(
    sections: list[dict[str, str]],
    include_methods: bool = False,
    max_words: int = 4000,
) -> list[dict[str, str]]:
    """Select the most relevant paper sections within a word budget."""
    target = set(DEFAULT_SECTIONS)
    if include_methods:
        target.add("methods")

    candidates = [s for s in sections if s["canonical"] in target]

    if not candidates:
        candidates = [s for s in sections if s["canonical"] not in SKIP_SECTIONS and s["canonical"] != "other"]

    selected = []
    word_count = 0
    for s in candidates:
        words = len(s["text"].split())
        if word_count + words > max_words and selected:
            break
        selected.append(s)
        word_count += words

    return selected
