import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.search_service import SearchResult, reconstruct_abstract, deduplicate_academic_results


def test_search_result_academic_fields_default():
    r = SearchResult(title="Test", url="https://example.com", content="Abstract")
    assert r.is_academic is False
    assert r.authors is None
    assert r.year is None
    assert r.venue is None
    assert r.citation_count is None
    assert r.doi is None


def test_search_result_academic_fields_populated():
    r = SearchResult(
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        content="The dominant sequence transduction models...",
        score=0.95,
        authors=["Vaswani, A.", "Shazeer, N."],
        year=2017,
        venue="NeurIPS",
        citation_count=90000,
        doi="10.48550/arXiv.1706.03762",
        is_academic=True,
    )
    assert r.is_academic is True
    assert r.authors == ["Vaswani, A.", "Shazeer, N."]
    assert r.year == 2017
    assert r.citation_count == 90000


def test_reconstruct_abstract_basic():
    inverted = {"Machine": [0], "learning": [1], "is": [2], "great": [3]}
    assert reconstruct_abstract(inverted) == "Machine learning is great"


def test_reconstruct_abstract_repeated_words():
    inverted = {"the": [0, 4], "cat": [1], "sat": [2], "on": [3], "mat": [5]}
    assert reconstruct_abstract(inverted) == "the cat sat on the mat"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract({}) == ""
    assert reconstruct_abstract(None) == ""


def test_dedup_by_doi():
    results = [
        SearchResult(title="Paper A", url="https://s2.com/1", content="Abstract A",
                     doi="10.1234/a", is_academic=True, authors=["Smith"], year=2023,
                     venue="NeurIPS", citation_count=100),
        SearchResult(title="Paper A", url="https://openalex.org/1", content="Abstract A",
                     doi="10.1234/a", is_academic=True, authors=["Smith"], year=2023),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 1
    assert deduped[0].citation_count == 100


def test_dedup_by_title_similarity():
    results = [
        SearchResult(title="Attention Is All You Need", url="https://s2.com/1",
                     content="Abstract", doi="10.1234/a", is_academic=True,
                     authors=["Vaswani"], citation_count=90000),
        SearchResult(title="Attention is All You Need.", url="https://arxiv.org/1",
                     content="Abstract", doi=None, is_academic=True),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 1
    assert deduped[0].citation_count == 90000


def test_dedup_different_papers():
    results = [
        SearchResult(title="Paper A", url="u1", content="c1", doi="10.1/a", is_academic=True),
        SearchResult(title="Paper B", url="u2", content="c2", doi="10.1/b", is_academic=True),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 2


@pytest.mark.asyncio
async def test_search_semantic_scholar_parses_response():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 1,
        "offset": 0,
        "data": [
            {
                "paperId": "abc123",
                "title": "Test Paper",
                "url": "https://www.semanticscholar.org/paper/abc123",
                "abstract": "This is a test abstract about machine learning.",
                "year": 2023,
                "authors": [{"authorId": "1", "name": "Smith, J."}, {"authorId": "2", "name": "Lee, K."}],
                "venue": "NeurIPS",
                "citationCount": 42,
                "externalIds": {"DOI": "10.1234/test"},
                "openAccessPdf": {"url": "https://example.com/paper.pdf", "status": "GREEN"},
                "publicationTypes": ["JournalArticle"],
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            query="machine learning",
            credentials={},
            max_results=5,
            search_depth="basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper"
    assert r.is_academic is True
    assert r.authors == ["Smith, J.", "Lee, K."]
    assert r.year == 2023
    assert r.venue == "NeurIPS"
    assert r.citation_count == 42
    assert r.doi == "10.1234/test"
    assert "test abstract" in r.content


@pytest.mark.asyncio
async def test_search_semantic_scholar_skips_null_abstract():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 2, "offset": 0,
        "data": [
            {"paperId": "1", "title": "No Abstract", "url": "u1", "abstract": None,
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
            {"paperId": "2", "title": "Has Abstract", "url": "u2", "abstract": "Real content",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            "test", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    assert results[0].title == "Has Abstract"


@pytest.mark.asyncio
async def test_search_semantic_scholar_open_access_filter():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 2, "offset": 0,
        "data": [
            {"paperId": "1", "title": "OA Paper", "url": "u1", "abstract": "Abstract 1",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": {"url": "https://pdf.com", "status": "GREEN"},
             "publicationTypes": []},
            {"paperId": "2", "title": "Closed Paper", "url": "u2", "abstract": "Abstract 2",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            "test", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": True},
        )

    assert len(results) == 1
    assert results[0].title == "OA Paper"
