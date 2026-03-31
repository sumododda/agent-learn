from datetime import date

import pytest

from app.academic_search import (
    AcademicResult,
    deduplicate_results,
    reconstruct_abstract,
    rerank_results,
    score_result,
    select_for_discovery,
    rank_for_deep_reading,
)


def test_academic_result_defaults():
    r = AcademicResult(title="Test", url="http://ex.com", abstract="Abstract text", authors=["A"])
    assert r.year is None
    assert r.citation_count is None
    assert r.doi is None
    assert r.pdf_url is None
    assert r.score == 0.0


def test_reconstruct_abstract():
    inverted = {"Machine": [0], "learning": [1], "is": [2], "great": [3]}
    assert reconstruct_abstract(inverted) == "Machine learning is great"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_deduplicate_by_doi():
    r1 = AcademicResult(title="Paper A", url="http://a", abstract="...", authors=["X"], doi="10.1/a", year=2024, citation_count=10)
    r2 = AcademicResult(title="Paper A (copy)", url="http://b", abstract="...", authors=["X"], doi="10.1/a", year=2024, venue="NeurIPS", citation_count=10)
    results = deduplicate_results([r1, r2])
    assert len(results) == 1
    assert results[0].venue == "NeurIPS"  # richer metadata wins


def test_deduplicate_by_title():
    r1 = AcademicResult(title="Attention Is All You Need", url="http://a", abstract="...", authors=["V"])
    r2 = AcademicResult(title="attention is all you need", url="http://b", abstract="...", authors=["V"], citation_count=100)
    results = deduplicate_results([r1, r2])
    assert len(results) == 1
    assert results[0].citation_count == 100


def test_rank_for_deep_reading_no_pdf():
    r = AcademicResult(title="T", url="u", abstract="a", authors=["A"], pdf_url=None)
    assert rank_for_deep_reading(r) == -1


def test_rank_for_deep_reading_with_pdf():
    r = AcademicResult(title="T", url="u", abstract="a", authors=["A"], pdf_url="http://pdf", citation_count=100, year=date.today().year)
    score = rank_for_deep_reading(r)
    assert score > 0


def test_score_result_penalizes_missing_signal_terms():
    r = AcademicResult(title="Cooking recipes", url="u", abstract="How to bake a cake", authors=["Chef"], year=2024)
    score = score_result(r, query="agent security vulnerabilities")
    assert score < 5.0


def test_rerank_orders_by_score():
    r1 = AcademicResult(title="agent security framework", url="u", abstract="security agents vulnerabilities", authors=["A"], year=2024, citation_count=50)
    r2 = AcademicResult(title="cooking tips", url="u2", abstract="how to cook", authors=["B"], year=2020, citation_count=5)
    ranked = rerank_results([r2, r1], query="agent security")
    assert ranked[0].title == "agent security framework"


def test_select_for_discovery_limits():
    results = [
        AcademicResult(title=f"Paper {i}", url=f"u{i}", abstract="abs", authors=["A"], year=2024 - i, citation_count=i * 10)
        for i in range(20)
    ]
    selected = select_for_discovery(results, query="test", limit=5)
    assert len(selected) == 5


import httpx
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_openalex_adapter_maps_fields():
    from app.academic_search import _search_openalex

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "meta": {"count": 1},
        "results": [
            {
                "title": "Test Paper",
                "display_name": "Test Paper",
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/test",
                "relevance_score": 15.5,
                "publication_year": 2024,
                "cited_by_count": 42,
                "authorships": [
                    {"author": {"display_name": "Alice Smith"}},
                    {"author": {"display_name": "Bob Jones"}},
                ],
                "abstract_inverted_index": {"Test": [0], "abstract": [1], "here": [2]},
                "primary_location": {
                    "landing_page_url": "https://example.com/paper",
                    "pdf_url": "https://example.com/paper.pdf",
                    "source": {"display_name": "Nature"},
                },
                "open_access": {"oa_url": None},
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.OPENALEX_API_KEY = "test-key"
            results = await _search_openalex("machine learning", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper"
    assert r.authors == ["Alice Smith", "Bob Jones"]
    assert r.year == 2024
    assert r.citation_count == 42
    assert r.doi == "10.1234/test"
    assert r.venue == "Nature"
    assert r.pdf_url == "https://example.com/paper.pdf"
    assert r.abstract == "Test abstract here"
    assert r.score == 15.5

    # Verify API key passed as query param
    call_kwargs = mock_client.get.call_args
    assert call_kwargs.kwargs["params"]["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_openalex_adapter_skips_no_abstract():
    from app.academic_search import _search_openalex

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "meta": {"count": 1},
        "results": [{"title": "No Abstract", "abstract_inverted_index": None, "authorships": [], "primary_location": None, "open_access": {}}],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.OPENALEX_API_KEY = "test-key"
            results = await _search_openalex("test", max_results=5)

    assert len(results) == 0


@pytest.mark.asyncio
async def test_openalex_adapter_returns_empty_when_no_key():
    from app.academic_search import _search_openalex

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.OPENALEX_API_KEY = ""
        results = await _search_openalex("test", max_results=5)

    assert results == []


@pytest.mark.asyncio
async def test_serper_scholar_adapter_maps_fields():
    from app.academic_search import _search_serper_scholar

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "searchParameters": {"q": "test", "type": "scholar"},
        "organic": [
            {
                "title": "Attention Is All You Need",
                "link": "https://proceedings.neurips.cc/paper/2017/123",
                "snippet": "The dominant sequence transduction models...",
                "publicationInfo": "A Vaswani, N Shazeer, N Parmar - Advances in neural information processing systems, 2017 - proceedings.neurips.cc",
                "citedBy": 119097,
                "year": 2017,
                "pdfUrl": "https://example.com/paper.pdf",
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.SERPER_API_KEY = "test-key"
            results = await _search_serper_scholar("attention mechanisms", max_results=10)

    assert len(results) == 1
    r = results[0]
    assert r.title == "Attention Is All You Need"
    assert r.url == "https://proceedings.neurips.cc/paper/2017/123"
    assert r.abstract == "The dominant sequence transduction models..."
    assert r.citation_count == 119097
    assert r.year == 2017
    assert r.pdf_url == "https://example.com/paper.pdf"
    assert r.doi is None  # No DOI extractable from this URL
    assert "Vaswani" in r.authors[0]
    assert r.venue is not None

    # Verify correct endpoint and headers
    call_kwargs = mock_client.post.call_args
    assert "scholar" in str(call_kwargs.args[0])
    assert call_kwargs.kwargs["headers"]["X-API-KEY"] == "test-key"


@pytest.mark.asyncio
async def test_serper_scholar_parses_publication_info():
    from app.academic_search import _parse_publication_info

    authors, venue = _parse_publication_info(
        "A Vaswani, N Shazeer, N Parmar - Advances in neural information processing systems, 2017 - proceedings.neurips.cc"
    )
    assert len(authors) >= 3
    assert "A Vaswani" in authors
    assert venue is not None
    assert "neural" in venue.lower()


@pytest.mark.asyncio
async def test_serper_scholar_handles_missing_fields():
    from app.academic_search import _search_serper_scholar

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "organic": [
            {
                "title": "Minimal Paper",
                "link": "https://example.com",
                "snippet": "Some text",
                "publicationInfo": "Author Name - 2023",
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.SERPER_API_KEY = "test-key"
            results = await _search_serper_scholar("test", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.citation_count is None  # citedBy missing
    assert r.pdf_url is None  # pdfUrl missing


@pytest.mark.asyncio
async def test_serper_scholar_returns_empty_when_no_key():
    from app.academic_search import _search_serper_scholar

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.SERPER_API_KEY = ""
        results = await _search_serper_scholar("test", max_results=5)

    assert results == []


@pytest.mark.asyncio
async def test_unpaywall_enriches_missing_pdf_urls():
    from app.academic_search import _enrich_with_unpaywall, AcademicResult

    mock_response_with_pdf = MagicMock()
    mock_response_with_pdf.status_code = 200
    mock_response_with_pdf.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/paper.pdf",
            "url": "https://example.com/paper",
            "url_for_landing_page": "https://example.com/landing",
        },
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response_with_pdf)

    results = [
        AcademicResult(title="Paper A", url="u", abstract="a", authors=["X"], doi="10.1/a", pdf_url=None),
        AcademicResult(title="Paper B", url="u", abstract="a", authors=["X"], doi="10.1/b", pdf_url="http://existing.pdf"),
        AcademicResult(title="Paper C", url="u", abstract="a", authors=["X"], doi=None, pdf_url=None),
    ]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url == "https://example.com/paper.pdf"
    assert enriched[1].pdf_url == "http://existing.pdf"
    assert enriched[2].pdf_url is None
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_unpaywall_falls_back_to_url_when_pdf_null():
    from app.academic_search import _enrich_with_unpaywall, AcademicResult

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": None,
            "url": "https://example.com/oa-version",
            "url_for_landing_page": "https://example.com/landing",
        },
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Paper", url="u", abstract="a", authors=["X"], doi="10.1/x")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url == "https://example.com/oa-version"


@pytest.mark.asyncio
async def test_unpaywall_handles_closed_access():
    from app.academic_search import _enrich_with_unpaywall, AcademicResult

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_oa": False,
        "best_oa_location": None,
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Closed", url="u", abstract="a", authors=["X"], doi="10.1/closed")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None


@pytest.mark.asyncio
async def test_unpaywall_handles_404_gracefully():
    from app.academic_search import _enrich_with_unpaywall, AcademicResult
    import httpx as _httpx

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(side_effect=_httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Missing", url="u", abstract="a", authors=["X"], doi="10.1/missing")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None


@pytest.mark.asyncio
async def test_unpaywall_skips_when_no_email():
    from app.academic_search import _enrich_with_unpaywall, AcademicResult

    results = [AcademicResult(title="Paper", url="u", abstract="a", authors=["X"], doi="10.1/x")]

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.UNPAYWALL_EMAIL = ""
        enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None


@pytest.mark.asyncio
async def test_academic_search_parallel_both_providers():
    from app.academic_search import academic_search, AcademicResult

    openalex_results = [
        AcademicResult(title="OA Paper", url="http://oa", abstract="OA abstract", authors=["A"], doi="10.1/oa", year=2024, citation_count=50)
    ]
    serper_results = [
        AcademicResult(title="Serper Paper", url="http://serper", abstract="Serper abstract", authors=["B"], year=2023, citation_count=30)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=openalex_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=serper_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test query", max_results=10)

    assert len(results) == 2
    titles = {r.title for r in results}
    assert "OA Paper" in titles
    assert "Serper Paper" in titles


@pytest.mark.asyncio
async def test_academic_search_deduplicates_cross_provider():
    from app.academic_search import academic_search, AcademicResult

    openalex_results = [
        AcademicResult(title="Same Paper", url="http://oa", abstract="abstract", authors=["A"], doi="10.1/same", year=2024, citation_count=50, venue="NeurIPS")
    ]
    serper_results = [
        AcademicResult(title="Same Paper", url="http://serper", abstract="abstract", authors=["A"], year=2024, citation_count=50)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=openalex_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=serper_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=10)

    assert len(results) == 1
    assert results[0].venue == "NeurIPS"


@pytest.mark.asyncio
async def test_academic_search_one_provider_fails():
    from app.academic_search import academic_search, AcademicResult

    good_results = [
        AcademicResult(title="Good Paper", url="u", abstract="a", authors=["A"], year=2024)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, side_effect=Exception("API down")):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=good_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=10)

    assert len(results) == 1
    assert results[0].title == "Good Paper"


@pytest.mark.asyncio
async def test_academic_search_respects_max_results():
    from app.academic_search import academic_search, AcademicResult

    many_results = [
        AcademicResult(title=f"Paper {i}", url=f"u{i}", abstract="a", authors=["A"], year=2024)
        for i in range(20)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=many_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=[]):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=5)

    assert len(results) == 5
