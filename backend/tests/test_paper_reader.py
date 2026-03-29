import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import httpx

from app.paper_reader import normalize_section_name, download_pdf, select_sections


# --- Section normalization tests ---

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


# --- PDF download tests ---

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
        Path(path).unlink()


@pytest.mark.asyncio
async def test_download_pdf_failure():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")):
        path = await download_pdf("https://example.com/paper.pdf")
        assert path is None


# --- Section selection tests ---

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
    assert total_words <= 5001  # first section exceeds cap but is included since it's first
