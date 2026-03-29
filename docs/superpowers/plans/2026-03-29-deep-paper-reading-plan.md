# Deep Paper Reading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade academic search from abstract-only to full paper reading during section research, using Docling for PDF parsing and a paper reader LLM agent for structured finding extraction.

**Architecture:** Add `pdf_url` to SearchResult, rank papers for deep reading, download + parse with Docling, extract findings via paper reader agent, feed deep findings to section researcher for richer evidence cards. Also fix discovery metadata loss as a Layer 1 quick win.

**Tech Stack:** Python, Docling (PDF parsing), httpx (PDF download), existing LangChain agent framework

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/search_service.py` | Modify | Add `pdf_url` to `SearchResult`, populate in S2/arXiv/OpenAlex adapters, add `rank_for_deep_reading()` |
| `backend/app/paper_reader.py` | Create | PDF download, Docling parsing, section normalization, section selection, reader orchestration |
| `backend/app/agent.py` | Modify | `PaperReading` + `DeepFinding` schemas, `PAPER_READER_PROMPT`, `create_paper_reader()` |
| `backend/app/agent_service.py` | Modify | Deep reading in `research_section()`, discovery metadata fixes |
| `backend/requirements.txt` | Modify | Add `docling` |
| `backend/tests/test_paper_reader.py` | Create | Tests for ranking, section normalization, reader integration |

---

### Task 1: Add `pdf_url` to SearchResult and Populate in Adapters

**Files:**
- Modify: `backend/app/search_service.py:72-84` (SearchResult), `:404-480` (S2), `:500-587` (arXiv), `:601-676` (OpenAlex)
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write tests for pdf_url field**

Append to `backend/tests/test_academic_search.py`:

```python
def test_search_result_pdf_url_default():
    r = SearchResult(title="T", url="u", content="c")
    assert r.pdf_url is None


def test_search_result_pdf_url_populated():
    r = SearchResult(title="T", url="u", content="c", pdf_url="https://arxiv.org/pdf/1234.pdf")
    assert r.pdf_url == "https://arxiv.org/pdf/1234.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py::test_search_result_pdf_url_default -v`
Expected: FAIL — `SearchResult` does not accept `pdf_url`

- [ ] **Step 3: Add `pdf_url` field to SearchResult**

In `backend/app/search_service.py`, add after `is_academic: bool = False` (line ~84):

```python
    pdf_url: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Update Semantic Scholar adapter to populate pdf_url**

In `_search_semantic_scholar` (around line 468-479), in the `SearchResult` construction, add:

```python
            pdf_url=(paper.get("openAccessPdf") or {}).get("url"),
```

Add after the `is_academic=True` line.

- [ ] **Step 6: Update arXiv adapter to populate pdf_url**

In `_search_arxiv` (around line 565-572), after the existing link loop that finds the `rel="alternate"` URL, add a separate extraction for the PDF link:

```python
        pdf_link = ""
        for link_el in entry.findall("atom:link", ns):
            if link_el.get("title") == "pdf":
                pdf_link = (link_el.get("href", "")).replace("http://", "https://")
                break
```

Then in the `SearchResult` construction, add:

```python
            pdf_url=pdf_link or None,
```

- [ ] **Step 7: Update OpenAlex adapter to populate pdf_url**

In `_search_openalex` (around line 657-661), extract the PDF URL:

```python
        pdf_url = loc.get("pdf_url") or (work.get("open_access") or {}).get("oa_url")
```

Then in the `SearchResult` construction, add:

```python
            pdf_url=pdf_url,
```

- [ ] **Step 8: Update adapter tests to verify pdf_url**

Update the existing mock responses in the S2, arXiv, and OpenAlex tests to assert `pdf_url` is populated correctly. For S2 test, assert `r.pdf_url == "https://example.com/paper.pdf"`. For arXiv test, add a `<link title="pdf" href="http://arxiv.org/pdf/1706.03762v5" rel="related"/>` to the mock XML and assert `r.pdf_url == "https://arxiv.org/pdf/1706.03762v5"`. For OpenAlex test, assert `r.pdf_url == "https://nature.com/articles/test.pdf"` (from `primary_location.pdf_url` in the mock).

- [ ] **Step 9: Run all tests and commit**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add pdf_url to SearchResult, populate in all academic adapters"
```

---

### Task 2: Paper Ranking Function

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write tests for ranking**

Append to `backend/tests/test_academic_search.py`:

```python
from app.search_service import rank_for_deep_reading


def test_rank_excludes_no_pdf():
    r = SearchResult(title="T", url="u", content="c", is_academic=True,
                     citation_count=1000, year=2023, pdf_url=None)
    assert rank_for_deep_reading(r) == -1


def test_rank_higher_for_more_citations():
    r1 = SearchResult(title="T", url="u", content="c", is_academic=True,
                      citation_count=100, year=2023, pdf_url="https://pdf.com/1")
    r2 = SearchResult(title="T", url="u", content="c", is_academic=True,
                      citation_count=1000, year=2023, pdf_url="https://pdf.com/2")
    assert rank_for_deep_reading(r2) > rank_for_deep_reading(r1)


def test_rank_recency_boost():
    # Recent paper with fewer citations can beat older paper
    old = SearchResult(title="T", url="u", content="c", is_academic=True,
                       citation_count=200, year=2018, pdf_url="https://pdf.com/1")
    new = SearchResult(title="T", url="u", content="c", is_academic=True,
                       citation_count=80, year=2025, pdf_url="https://pdf.com/2")
    assert rank_for_deep_reading(new) > rank_for_deep_reading(old)


def test_rank_log_scale_diminishing_returns():
    r1 = SearchResult(title="T", url="u", content="c", is_academic=True,
                      citation_count=1000, year=2023, pdf_url="https://pdf.com/1")
    r2 = SearchResult(title="T", url="u", content="c", is_academic=True,
                      citation_count=10000, year=2023, pdf_url="https://pdf.com/2")
    ratio = rank_for_deep_reading(r2) / rank_for_deep_reading(r1)
    # 10x more citations should NOT give 10x higher score (log scale)
    assert ratio < 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py::test_rank_excludes_no_pdf -v`
Expected: FAIL — `rank_for_deep_reading` not defined

- [ ] **Step 3: Implement ranking function**

Add to `backend/app/search_service.py` after the `deduplicate_academic_results` function:

```python
import math


def rank_for_deep_reading(result: SearchResult) -> float:
    """Rank an academic paper for deep reading priority.

    Returns -1 if paper has no open-access PDF (cannot be read).
    Otherwise returns log(citations) * recency_boost.
    """
    if not result.pdf_url:
        return -1

    citations = result.citation_count or 0
    year = result.year or 2020
    from datetime import date
    age = date.today().year - year

    if age <= 2:
        recency = 3.0
    elif age <= 5:
        recency = 2.0
    else:
        recency = 1.0

    return math.log1p(citations) * recency
```

- [ ] **Step 4: Run tests and commit**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add rank_for_deep_reading with citation count + recency boost"
```

---

### Task 3: Docling Integration — PDF Download, Parse, Section Normalize

**Files:**
- Create: `backend/app/paper_reader.py`
- Create: `backend/tests/test_paper_reader.py`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add docling dependency**

Append to `backend/requirements.txt`:

```
docling>=2.80.0
```

Install: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/pip install 'docling>=2.80.0'`

- [ ] **Step 2: Write tests for section name normalization**

Create `backend/tests/test_paper_reader.py`:

```python
from app.paper_reader import normalize_section_name


def test_normalize_methods():
    assert normalize_section_name("3. Experimental Setup") == "methods"
    assert normalize_section_name("Methodology") == "methods"
    assert normalize_section_name("IV. APPROACH AND IMPLEMENTATION") == "methods"


def test_normalize_results():
    assert normalize_section_name("Results") == "results"
    assert normalize_section_name("5. Evaluation and Results") == "results"
    assert normalize_section_name("Performance") == "results"


def test_normalize_discussion():
    assert normalize_section_name("Discussion") == "discussion"
    assert normalize_section_name("6. Analysis and Discussion") == "discussion"


def test_normalize_conclusion():
    assert normalize_section_name("Conclusion") == "conclusion"
    assert normalize_section_name("7. Conclusions and Future Work") == "conclusion"
    assert normalize_section_name("Summary") == "conclusion"


def test_normalize_introduction():
    assert normalize_section_name("1. Introduction") == "introduction"
    assert normalize_section_name("Background") == "introduction"


def test_normalize_related_work():
    assert normalize_section_name("Related Work") == "related_work"
    assert normalize_section_name("2. Literature Review") == "related_work"


def test_normalize_unknown():
    assert normalize_section_name("Appendix A") == "other"
    assert normalize_section_name("Acknowledgements") == "other"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_paper_reader.py -v`
Expected: FAIL — `paper_reader` module not found

- [ ] **Step 4: Implement paper_reader.py with section normalization**

Create `backend/app/paper_reader.py`:

```python
"""PDF download, Docling parsing, section normalization, and paper reader orchestration."""
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
    # Strip numbering like "3.", "IV.", "3.1"
    cleaned = re.sub(r"^[\dIVXivx]+[\.\)]\s*", "", heading).strip()
    lower = cleaned.lower()

    for canonical, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return canonical
    return "other"
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_paper_reader.py -v`
Expected: All PASS

- [ ] **Step 6: Write tests for PDF download**

Append to `backend/tests/test_paper_reader.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.paper_reader import download_pdf


@pytest.mark.asyncio
async def test_download_pdf_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"%PDF-1.4 fake pdf content"
    mock_resp.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        path = await download_pdf("https://example.com/paper.pdf")
        assert path is not None
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"%PDF-1.4 fake pdf content"
        Path(path).unlink()  # cleanup


@pytest.mark.asyncio
async def test_download_pdf_failure():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")):
        path = await download_pdf("https://example.com/paper.pdf")
        assert path is None
```

- [ ] **Step 7: Implement download_pdf**

Add to `backend/app/paper_reader.py`:

```python
async def download_pdf(url: str) -> str | None:
    """Download a PDF from a URL to a temp file. Returns path or None on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=30.0)
            resp.raise_for_status()

        suffix = ".pdf"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(resp.content)
        tmp.close()
        logger.info("[paper_reader] Downloaded PDF (%d bytes) to %s", len(resp.content), tmp.name)
        return tmp.name
    except Exception as e:
        logger.warning("[paper_reader] Failed to download PDF from %s: %s", url, e)
        return None
```

- [ ] **Step 8: Run tests and verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_paper_reader.py -v`
Expected: All PASS

- [ ] **Step 9: Write test for parse_pdf_sections**

Append to `backend/tests/test_paper_reader.py`:

```python
from app.paper_reader import parse_pdf_sections


@pytest.mark.asyncio
async def test_parse_pdf_sections_returns_list():
    # Test with a non-existent file — should return empty list
    result = await parse_pdf_sections("/nonexistent/file.pdf")
    assert result == []
```

- [ ] **Step 10: Implement parse_pdf_sections with Docling**

Add to `backend/app/paper_reader.py`:

```python
import asyncio


async def parse_pdf_sections(pdf_path: str) -> list[dict[str, str]]:
    """Parse a PDF into labeled sections using Docling.

    Returns list of {heading: str, text: str, canonical: str} dicts.
    Returns empty list on failure.
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
                    # Save previous section
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

            # Save last section
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
```

- [ ] **Step 11: Write test for select_sections**

Append to `backend/tests/test_paper_reader.py`:

```python
from app.paper_reader import select_sections


def test_select_sections_defaults():
    sections = [
        {"heading": "Introduction", "text": "Intro text", "canonical": "introduction"},
        {"heading": "Methods", "text": "Methods text", "canonical": "methods"},
        {"heading": "Results", "text": "Results text " * 100, "canonical": "results"},
        {"heading": "Discussion", "text": "Discussion text", "canonical": "discussion"},
        {"heading": "Conclusion", "text": "Conclusion text", "canonical": "conclusion"},
        {"heading": "References", "text": "Ref text", "canonical": "other"},
    ]
    selected = select_sections(sections, include_methods=False)
    canonical_names = [s["canonical"] for s in selected]
    assert "results" in canonical_names
    assert "discussion" in canonical_names
    assert "conclusion" in canonical_names
    assert "introduction" not in canonical_names
    assert "other" not in canonical_names


def test_select_sections_with_methods():
    sections = [
        {"heading": "Methods", "text": "Methods text", "canonical": "methods"},
        {"heading": "Results", "text": "Results text", "canonical": "results"},
    ]
    selected = select_sections(sections, include_methods=True)
    canonical_names = [s["canonical"] for s in selected]
    assert "methods" in canonical_names
    assert "results" in canonical_names


def test_select_sections_word_cap():
    sections = [
        {"heading": "Results", "text": "word " * 5000, "canonical": "results"},
        {"heading": "Discussion", "text": "short text", "canonical": "discussion"},
    ]
    selected = select_sections(sections, max_words=4000)
    total_words = sum(len(s["text"].split()) for s in selected)
    assert total_words <= 4000
```

- [ ] **Step 12: Implement select_sections**

Add to `backend/app/paper_reader.py`:

```python
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

    # Filter to target sections
    candidates = [s for s in sections if s["canonical"] in target]

    # If no recognized sections found, return all non-skip sections
    if not candidates:
        candidates = [s for s in sections if s["canonical"] not in SKIP_SECTIONS and s["canonical"] != "other"]

    # Trim to word budget
    selected = []
    word_count = 0
    for s in candidates:
        words = len(s["text"].split())
        if word_count + words > max_words and selected:
            break
        selected.append(s)
        word_count += words

    return selected
```

- [ ] **Step 13: Run all tests and commit**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_paper_reader.py -v`
Expected: All PASS

```bash
git add backend/app/paper_reader.py backend/tests/test_paper_reader.py backend/requirements.txt
git commit -m "feat: add paper_reader with PDF download, Docling parsing, section normalization"
```

---

### Task 4: Paper Reader LLM Agent

**Files:**
- Modify: `backend/app/agent.py`
- Modify: `backend/app/paper_reader.py`
- Modify: `backend/tests/test_paper_reader.py`

- [ ] **Step 1: Add PaperReading and DeepFinding schemas to agent.py**

In `backend/app/agent.py`, add after the existing `EvidenceCardSet` class:

```python
class DeepFinding(BaseModel):
    claim: str
    supporting_text: str
    paper_section: str
    data_point: str | None = None
    finding_type: str  # "quantitative_result" | "methodology" | "theoretical" | "observation"
    answers_question: str


class PaperReading(BaseModel):
    findings: list[DeepFinding]
    methodology_summary: str
    limitations: list[str]
```

- [ ] **Step 2: Add PAPER_READER_PROMPT**

Add to `backend/app/agent.py`:

```python
PAPER_READER_PROMPT = """You are a research paper reader. You receive sections of an academic paper
and research questions. Your job is to extract specific, concrete findings from the paper that
answer the research questions.

RULES:
- Extract 3-8 findings per paper, targeted to the provided research questions.
- `supporting_text` MUST be a real passage from the provided paper text, not paraphrased.
  Copy the relevant sentences exactly as they appear.
- `data_point` is REQUIRED when the paper contains specific numbers, metrics, or benchmarks.
  Example: "94.3% accuracy on MMLU", "3.2x speedup over baseline", "p < 0.001".
- `finding_type` must be one of: "quantitative_result", "methodology", "theoretical", "observation".
- `answers_question` must reference which research question this finding addresses.
- `methodology_summary`: 2-3 sentences on how the research was conducted. Be specific about
  models, datasets, and evaluation methods used.
- `limitations`: only what the authors EXPLICITLY state as limitations. Do not invent limitations.
  If no limitations are mentioned, return an empty list.

Focus on what the abstract CANNOT tell you: specific numbers, methodology details, limitations,
nuanced findings, and supporting evidence."""
```

- [ ] **Step 3: Add create_paper_reader function**

Add to `backend/app/agent.py`:

```python
def create_paper_reader(provider: str, model: str, credentials: dict, extra_fields: dict | None = None):
    """Create a paper reader agent with structured PaperReading output."""
    logger.info("[agent:paper_reader] Creating paper reader agent (model=%s)", model)
    llm = provider_service.build_chat_model(provider, model, credentials, extra_fields)
    return create_agent(
        model=llm,
        system_prompt=PAPER_READER_PROMPT,
        response_format=ToolStrategy(PaperReading),
        name="agent-learn-paper-reader",
    )
```

- [ ] **Step 4: Verify imports work**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -c "from app.agent import create_paper_reader, PaperReading, DeepFinding; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Add read_paper orchestration to paper_reader.py**

Add to `backend/app/paper_reader.py`:

```python
from app.search_service import SearchResult


async def read_paper(
    result: SearchResult,
    questions: list[str],
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict | None:
    """Download, parse, and deep-read a single paper. Returns PaperReading dict or None on failure."""
    from app.agent import create_paper_reader, PaperReading

    if not result.pdf_url:
        return None

    # Download
    pdf_path = await download_pdf(result.pdf_url)
    if not pdf_path:
        return None

    try:
        # Parse with Docling
        sections = await parse_pdf_sections(pdf_path)
        if not sections:
            logger.warning("[paper_reader] No sections extracted from %s", result.title)
            return None

        # Check if any question asks about methodology
        include_methods = any(
            kw in q.lower() for q in questions for kw in ["how", "method", "approach", "technique", "implement"]
        )
        selected = select_sections(sections, include_methods=include_methods)
        if not selected:
            logger.warning("[paper_reader] No relevant sections found in %s", result.title)
            return None

        # Build reader prompt
        message = f"Research questions for this section:\n"
        for i, q in enumerate(questions, 1):
            message += f"{i}. {q}\n"

        authors_str = ", ".join(result.authors) if result.authors else "Unknown"
        message += f"\nPaper: \"{result.title}\" ({authors_str}, {result.year or 'n.d.'})\n"

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
        # Cleanup temp file
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except Exception:
            pass
```

- [ ] **Step 6: Add deep_read_top_papers orchestration**

Add to `backend/app/paper_reader.py`:

```python
from app.search_service import rank_for_deep_reading


async def deep_read_top_papers(
    academic_results: list[SearchResult],
    questions: list[str],
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    max_papers: int = 3,
) -> list[dict]:
    """Select top papers by ranking, download, parse, and deep-read them.

    Returns list of {search_result: SearchResult, reading: dict} for successfully read papers.
    """
    # Rank and select top papers with open-access PDFs
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
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent.py backend/app/paper_reader.py
git commit -m "feat: add paper reader agent with PaperReading schema and orchestration"
```

---

### Task 5: Integrate Deep Reading into Section Research

**Files:**
- Modify: `backend/app/agent_service.py:549-630`

- [ ] **Step 1: Add deep reading call in research_section**

In `backend/app/agent_service.py`, in the `research_section()` function, after the academic search block (around line 613) and before the section researcher invocation (around line 628), add:

```python
    # Deep-read top papers (if academic search is enabled and returned results)
    deep_readings: list[dict] = []
    if academic_credentials and academic_options and academic_options.get("enabled"):
        academic_search_results = [
            r for r in all_raw_results if getattr(r, "is_academic", False)
        ]
        if academic_search_results:
            from app.paper_reader import deep_read_top_papers
            deep_readings = await deep_read_top_papers(
                academic_search_results,
                brief.questions,
                provider, model, credentials or {}, extra_fields,
                max_papers=3,
            )
            if deep_readings:
                logger.info("[research] Section %d: deep-read %d papers", brief.section_position, len(deep_readings))
```

Note: This requires keeping the raw `SearchResult` objects from academic search in a separate list (`all_raw_results`) before they're converted to dicts. Modify the academic search block (lines 598-613) to also preserve the raw results:

```python
    all_raw_results: list = []  # Add near top of function

    # In the academic search block, after extending all_results:
    if academic_credentials and academic_options and academic_options.get("enabled"):
        from app.search_service import academic_search as run_academic_search
        for question in brief.questions:
            try:
                acad_results = await run_academic_search(
                    question, academic_credentials, academic_options, max_results=5,
                )
                all_raw_results.extend(acad_results)
                for r in acad_results:
                    all_results.append({
                        "title": f"[ACADEMIC] {r.title}",
                        "url": r.url,
                        "content": r.content,
                        "score": r.score,
                    })
            except Exception as e:
                logger.warning("[research] Academic search failed for '%s': %s", question[:60], e)
```

- [ ] **Step 2: Format deep readings for the researcher prompt**

After the deep reading block, format the readings and append to the researcher message:

```python
    # Format deep readings for the researcher
    if deep_readings:
        deep_text = "\n\nDeep paper readings:\n"
        for dr in deep_readings:
            sr = dr["search_result"]
            reading = dr["reading"]
            authors_str = ", ".join(sr.authors) if sr.authors else "Unknown"
            deep_text += f'\n[DEEP-READ] "{sr.title}" ({authors_str}, {sr.year or "n.d."})\n'
            deep_text += f'  Methodology: {reading.get("methodology_summary", "N/A")}\n'
            lims = reading.get("limitations", [])
            if lims:
                deep_text += f'  Limitations: {"; ".join(lims)}\n'
            for i, f in enumerate(reading.get("findings", []), 1):
                deep_text += f'\n  Finding {i} ({f.get("finding_type", "observation")}, answers: {f.get("answers_question", "N/A")}):\n'
                deep_text += f'    Claim: {f.get("claim", "")}\n'
                if f.get("data_point"):
                    deep_text += f'    Data: {f["data_point"]}\n'
                deep_text += f'    Source section: {f.get("paper_section", "Unknown")}\n'
                deep_text += f'    Supporting text: "{f.get("supporting_text", "")}"\n'

        # Append to the message that goes to the section researcher
        message += deep_text
```

- [ ] **Step 3: Update section researcher prompt**

In `backend/app/agent.py`, append to `SECTION_RESEARCHER_PROMPT` (around line 259):

```
When deep paper readings ([DEEP-READ] entries) are provided, prefer them over abstract-based
results for evidence cards. Deep readings contain verified passages from the actual papers.
Set source_tier=1, is_academic=True, and confidence >= 0.9 for evidence cards derived from
deep readings. Use the supporting_text as the passage field — it is a real quote from the paper.
```

- [ ] **Step 4: Verify no import errors**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -c "from app.agent_service import research_section; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_service.py backend/app/agent.py
git commit -m "feat: integrate deep paper reading into section research phase"
```

---

### Task 6: Layer 1 — Discovery Metadata Fixes

**Files:**
- Modify: `backend/app/agent_service.py:221-240`
- Modify: `backend/app/agent.py` (DISCOVERY_RESEARCHER_PROMPT)

- [ ] **Step 1: Fix metadata loss in discover_topic**

In `backend/app/agent_service.py`, replace the academic results loop in `discover_topic()` (lines 232-238) with:

```python
    # Sort academic results by citation count (highest first)
    academic_results.sort(key=lambda r: r.citation_count or 0, reverse=True)
    # Take top 10 academic results
    for r in academic_results[:10]:
        all_search_results.append({
            "title": f"[ACADEMIC] {r.title}",
            "url": r.url,
            "content": r.content,
            "score": r.score,
            "authors": ", ".join(r.authors) if r.authors else None,
            "year": r.year,
            "venue": r.venue,
            "citations": r.citation_count,
            "doi": r.doi,
        })
```

- [ ] **Step 2: Cap total results at 25**

After the academic results block, before the researcher invocation, add:

```python
    # Cap total results: top 10 academic (already added) + top 15 web by score
    web_results = [r for r in all_search_results if not r.get("title", "").startswith("[ACADEMIC]")]
    academic_results_list = [r for r in all_search_results if r.get("title", "").startswith("[ACADEMIC]")]
    web_results.sort(key=lambda r: r.get("score", 0), reverse=True)
    all_search_results = academic_results_list + web_results[:15]
```

- [ ] **Step 3: Update discovery researcher prompt**

In `backend/app/agent.py`, append to the discovery researcher prompt:

```
Academic sources include metadata: authors, year, venue, and citation count. Weight highly-cited
papers from prestigious venues (Nature, Science, NeurIPS, ICML, ACL, etc.) more heavily when
identifying key concepts and learning progression. Ensure balanced topic coverage — do not
over-index on topics that appear frequently in search results at the expense of important but
less-mentioned topics.
```

- [ ] **Step 4: Also fix metadata in research_section**

In `backend/app/agent_service.py`, in `research_section()`, update the academic results loop (around lines 605-611) to include metadata:

```python
                for r in acad_results:
                    all_results.append({
                        "title": f"[ACADEMIC] {r.title}",
                        "url": r.url,
                        "content": r.content,
                        "score": r.score,
                        "authors": ", ".join(r.authors) if r.authors else None,
                        "year": r.year,
                        "venue": r.venue,
                        "citations": r.citation_count,
                        "doi": r.doi,
                    })
```

- [ ] **Step 5: Verify and commit**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -c "from app.agent_service import discover_topic, research_section; print('OK')"`
Expected: `OK`

```bash
git add backend/app/agent_service.py backend/app/agent.py
git commit -m "feat: fix discovery metadata loss, rank by citations, cap at 25 results"
```

---

### Task 7: End-to-End Verification

**Files:** No new files — verification only.

- [ ] **Step 1: Run all academic search tests**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/test_academic_search.py tests/test_paper_reader.py -v`
Expected: All PASS

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -m pytest tests/ -q`
Expected: All existing tests still pass (228+)

- [ ] **Step 3: Build frontend**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds (no frontend changes in this feature)

- [ ] **Step 4: Test Docling import**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -c "from docling.document_converter import DocumentConverter; print('Docling OK')"`
Expected: `Docling OK`

- [ ] **Step 5: Test full paper_reader import chain**

Run: `cd /Users/sumo/agent-learn/backend && /Users/sumo/agent-learn/backend/.venv/bin/python -c "
from app.paper_reader import download_pdf, parse_pdf_sections, select_sections, read_paper, deep_read_top_papers
from app.search_service import rank_for_deep_reading
from app.agent import create_paper_reader, PaperReading, DeepFinding
print('All imports OK')
"`
Expected: `All imports OK`

- [ ] **Step 6: Final commit with any fixes**

```bash
git add -A
git commit -m "fix: address any issues found during verification"
```
