# Deep Paper Reading — Design Spec

## Overview

Upgrade the academic search feature from abstract-only to full paper reading during section research (Phase 2). For the top 2-3 open-access papers per section, download the PDF, parse it with Docling into labeled sections (Methods, Results, Discussion, etc.), and send relevant sections to a paper reader LLM agent that extracts structured findings with real quotes, specific data, and methodology context. These deep findings produce richer evidence cards with actual paper content instead of abstract paraphrases.

Also includes Layer 1 quick fixes to discovery phase: pass academic metadata (authors, year, venue, citations) to the discovery researcher, rank by citations, cap results at 25.

## Paper Selection — Ranking Strategy

After abstract-level academic search per section, select top 2-3 papers for deep reading.

**Prerequisite:** Add `pdf_url: str | None = None` field to `SearchResult` dataclass. Each adapter populates it:
- Semantic Scholar: from `openAccessPdf.url` in API response
- OpenAlex: from `primary_location.pdf_url` or `open_access.oa_url`
- arXiv: extract from `<link rel="related" title="pdf" href="..."/>` in XML response (always present). Replace `http://` with `https://` in the href.

**Filter:** Must have `pdf_url` set. No PDF URL = skip.

**Rank by composite score:**

```python
import math

def rank_for_deep_reading(result: SearchResult) -> float:
    if not has_open_access_pdf(result):
        return -1

    citations = result.citation_count or 0
    year = result.year or 2020
    age = current_year - year

    # Recency boost: recent papers with fewer citations may be more significant
    if age <= 2:
        recency = 3.0
    elif age <= 5:
        recency = 2.0
    else:
        recency = 1.0

    # Log scale (diminishing returns: 10k citations isn't 10x better than 1k)
    return math.log1p(citations) * recency
```

Take top 2-3 by score. If `influentialCitationCount` is available from Semantic Scholar, add as tiebreaker.

## PDF Download & Parsing with Docling

**PDF Download:**
- Get PDF URL from SearchResult metadata
- Download to temp file with httpx, 30s timeout
- Skip on failure (graceful degradation to abstract-based evidence)

**Docling Parsing:**
- `pip install docling` (Python 3.10+, no external services)
- Parse PDF into structured document with labeled sections
- Each section: `{heading: str, text: str}`

**Section Name Normalization:**

Docling returns headings as written in the paper ("3. Experimental Setup", "IV. RESULTS AND DISCUSSION", etc.). A heuristic mapper normalizes these to canonical names:

```python
SECTION_KEYWORDS = {
    "methods": ["method", "approach", "experimental setup", "experiment", "implementation", "design"],
    "results": ["result", "finding", "evaluation", "performance", "experiment"],
    "discussion": ["discussion", "analysis", "interpretation"],
    "conclusion": ["conclusion", "summary", "concluding", "future work"],
    "introduction": ["introduction", "background", "overview", "motivation"],
    "related_work": ["related work", "literature review", "prior work", "state of the art"],
}
```

Strip numbering ("3.", "IV."), lowercase, match against keywords. Unrecognized sections labeled as `"other"`.

**Section Selection:**
- Default include: `results`, `discussion`, `conclusion` (highest value for course content)
- Include `methods` if a research question asks *how* something works
- Skip: `introduction` (mostly background we have from abstracts), References, Acknowledgements
- Cap at ~4,000 words per paper to manage LLM token cost
- If paper is shorter than 4,000 words total, feed the whole thing
- If Docling can't identify sections, feed raw text to reader agent

**Failure handling:**
- PDF download fails → skip paper, use abstract evidence
- Docling parsing fails → skip paper, use abstract evidence
- No recognizable sections → feed raw text (reader can handle messy input)

## Paper Reader Agent

New agent: `create_paper_reader()` in `agent.py`

**Input:**
```
Research questions for this section:
1. [question 1]
2. [question 2]
3. [question 3]

Paper: "Title" (Authors, Year)

--- METHODS ---
[Docling-extracted text]

--- RESULTS ---
[Docling-extracted text]

--- DISCUSSION ---
[Docling-extracted text]
```

**Output schema:**

```python
class PaperReading(BaseModel):
    """Paper metadata (title, authors, year, venue, doi, url) comes
    from SearchResult — NOT re-extracted by the LLM."""
    findings: list[DeepFinding]
    methodology_summary: str       # 2-3 sentences on how the research was conducted
    limitations: list[str]         # only what authors explicitly state


class DeepFinding(BaseModel):
    claim: str                     # specific, concrete finding
    supporting_text: str           # real passage from the paper
    paper_section: str             # "Results", "Methods", "Discussion", etc.
    data_point: str | None         # specific numbers/metrics if quantitative
    finding_type: str              # "quantitative_result" | "methodology" | "theoretical" | "observation"
    answers_question: str          # which research question this addresses
```

**Prompt instructions:**
- Extract 3-8 findings per paper, targeted to research questions
- `supporting_text` must be a real passage from the provided text, not paraphrased
- `data_point` required when paper contains specific numbers, metrics, benchmarks
- `methodology_summary`: how the research was conducted
- `limitations`: only what authors explicitly state, don't invent limitations

**Model:** Same as other agents (user's configured OpenRouter model).

**Called:** 2-3 times per section (one per paper), sequentially to manage token usage.

## Pipeline Integration

```
SECTION RESEARCH (Phase 2) — updated flow:

Per section:
  1. Web search per question (existing)
  2. Academic search per question (existing, abstracts only)
  3. Merge all results
  4. NEW: Select top 2-3 papers for deep reading
     → Filter: has open-access PDF
     → Rank: log(citations) x recencyBoost
     → Take top 2-3
  5. NEW: Download PDFs, parse with Docling into labeled sections
  6. NEW: For each paper, select relevant sections (Methods, Results, Discussion)
  7. NEW: Paper reader agent extracts DeepFindings per paper
  8. Pass search results + DeepFindings to section researcher
  9. Researcher extracts evidence cards (existing)
     → Deep findings become Tier 1 cards with richer passages
```

Discovery phase unchanged (abstracts only) plus Layer 1 metadata fixes.

No new pipeline checkpoint — deep reading is part of Phase 2.

## Evidence Card Integration

The section researcher receives two types of input:

1. **Search results** (web + abstract) — existing format
2. **Deep findings** — PaperReading objects attached to their source SearchResult

Deep findings are formatted for the researcher prompt as:

```
[DEEP-READ] "Paper Title" (Authors, Year)
  Methodology: ...
  Limitations: ...

  Finding 1 (quantitative_result, answers Q2):
    Claim: ...
    Data: ...
    Source section: Results
    Supporting text: "..."
```

**Researcher behavior:**
- Prefer deep findings over abstract-based evidence for the same question
- Deep findings become evidence cards with `source_tier=1`, `is_academic=True`, confidence 0.9+
- `supporting_text` from deep finding becomes the evidence card `passage` field
- Abstract-based academic results still become evidence cards for papers not deep-read

No changes to verification, writing, or editing — they work on evidence cards regardless of source.

## Layer 1: Discovery Metadata Quick Fixes

**Fix 1: Pass full academic metadata to discovery researcher**

Currently `agent_service.py` throws away authors, year, venue, citation_count, doi when building the search results list. Include them:

```python
for r in academic_results:
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

**Fix 2: Rank by citations before feeding.** Sort academic results by citation count descending. High-impact papers first in the prompt.

**Fix 3: Cap total results.** Limit to 25 results (top 10 academic by citations + top 15 web by relevance).

**Fix 4: Update discovery researcher prompt.** Add: "Academic sources include citation counts and venue information. Weight highly-cited papers from prestigious venues more heavily. Ensure balanced topic coverage."

## Files Changed

| File | Change |
|------|--------|
| `backend/app/search_service.py` | `pdf_url` field on `SearchResult`, `rank_for_deep_reading()`, populate `pdf_url` in S2/arXiv/OpenAlex adapters |
| `backend/app/paper_reader.py` | **New.** PDF download, Docling parsing, section name normalization, section selection, reader orchestration |
| `backend/app/agent.py` | `create_paper_reader()` agent, `PaperReading` + `DeepFinding` schemas, `PAPER_READER_PROMPT` |
| `backend/app/agent_service.py` | Deep reading in `research_section()`, discovery metadata fixes |
| `backend/requirements.txt` or `pyproject.toml` | Add `docling` dependency |
| `backend/tests/test_paper_reader.py` | **New.** Tests for ranking, download, parsing, reader output |

## What Stays the Same

- Discovery phase — abstracts only (plus Layer 1 metadata fixes)
- Pipeline checkpoints — no new checkpoints
- EvidenceCard model — deep findings use existing columns
- Verification, writing, editing — unchanged
- Frontend — no UI changes, deep reading is transparent
- Academic search adapters — unchanged

## Performance Impact

- Per section: +2-3 PDF downloads (~2-5s) + Docling parsing (~1-3s) + 2-3 LLM reader calls (~10-20s)
- Estimated overhead per section: ~30-60 seconds
- 7-section course: ~3.5-7 minutes additional
- Graceful fallback: if downloads/parsing fail, section uses abstract evidence only
