"""PDF download, Docling parsing, section normalization, and paper reader orchestration."""
import asyncio
import logging
import re
import tempfile
from pathlib import Path

import httpx

from app.academic_search import AcademicResult, rank_for_deep_reading

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

_PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/pdf,*/*",
}


async def download_pdf(url: str) -> str | None:
    """Download a PDF from a URL to a temp file. Returns path or None on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=_PDF_HEADERS) as client:
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


# ---------------------------------------------------------------------------
# Paper Reading Orchestration
# ---------------------------------------------------------------------------


async def read_paper(
    result: AcademicResult,
    questions: list[str],
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict | None:
    """Download, parse, and deep-read a single paper. Returns PaperReading dict or None."""
    from app.agent import create_paper_reader, PaperReading

    if not result.pdf_url:
        return None

    pdf_path = await download_pdf(result.pdf_url)
    if not pdf_path:
        return None

    try:
        sections = await parse_pdf_sections(pdf_path)
        if not sections:
            logger.warning("[paper_reader] No sections extracted from %s", result.title)
            return None

        include_methods = any(
            kw in q.lower() for q in questions for kw in ["how", "method", "approach", "technique", "implement"]
        )
        selected = select_sections(sections, include_methods=include_methods)
        if not selected:
            logger.warning("[paper_reader] No relevant sections found in %s", result.title)
            return None

        # Build reader prompt
        message = "Research questions for this section:\n"
        for i, q in enumerate(questions, 1):
            message += f"{i}. {q}\n"

        authors_str = ", ".join(result.authors) if result.authors else "Unknown"
        message += f'\nPaper: "{result.title}" ({authors_str}, {result.year or "n.d."})\n'

        for s in selected:
            message += f"\n--- {s['heading'].upper()} ---\n{s['text']}\n"

        # Invoke reader agent
        from app.agent_service import _invoke_agent
        reader = create_paper_reader(provider, model, credentials, extra_fields)
        reading = await _invoke_agent(reader, message)

        if isinstance(reading, PaperReading):
            return reading.model_dump()
        elif isinstance(reading, dict):
            return reading
        else:
            logger.warning("[paper_reader] Unexpected reader output type: %s", type(reading))
            return None

    except Exception as e:
        logger.warning("[paper_reader] Failed to read paper '%s': %s", result.title[:60], e)
        return None
    finally:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except Exception:
            pass


async def deep_read_top_papers(
    academic_results: list[AcademicResult],
    questions: list[str],
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    max_papers: int = 3,
) -> list[dict]:
    """Select top papers by ranking, download, parse, and deep-read them.
    Returns list of {search_result: AcademicResult, reading: dict}.
    """
    ranked = sorted(
        [r for r in academic_results if rank_for_deep_reading(r) > 0],
        key=rank_for_deep_reading,
        reverse=True,
    )[:max_papers]

    if not ranked:
        logger.info("[paper_reader] No papers with open-access PDFs available for deep reading")
        return []

    logger.info("[paper_reader] Deep reading %d papers: %s",
                len(ranked), [r.title[:40] for r in ranked])

    readings = []
    for result in ranked:
        reading = await read_paper(result, questions, provider, model, credentials, extra_fields)
        if reading:
            readings.append({"search_result": result, "reading": reading})

    logger.info("[paper_reader] Successfully read %d/%d papers", len(readings), len(ranked))
    return readings
