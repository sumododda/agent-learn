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
    assert r.pdf_url == "https://example.com/paper.pdf"
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


@pytest.mark.asyncio
async def test_search_arxiv_parses_xml():
    from app.search_service import _search_arxiv

    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">1</opensearch:totalResults>
      <entry>
        <id>http://arxiv.org/abs/1706.03762v5</id>
        <title>Attention Is All You Need</title>
        <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
        <published>2017-06-12T17:57:34Z</published>
        <author><name>Ashish Vaswani</name></author>
        <author><name>Noam Shazeer</name></author>
        <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
        <link href="http://arxiv.org/abs/1706.03762v5" rel="alternate" type="text/html"/>
        <link href="http://arxiv.org/pdf/1706.03762v5" title="pdf" type="application/pdf" rel="related"/>
      </entry>
    </feed>"""

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = xml_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = await _search_arxiv(
            "attention", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Attention Is All You Need"
    assert r.is_academic is True
    assert "Ashish Vaswani" in r.authors
    assert "Noam Shazeer" in r.authors
    assert r.year == 2017
    assert r.doi == "10.48550/arXiv.1706.03762"
    assert "sequence transduction" in r.content
    assert "arxiv.org" in r.url
    assert r.pdf_url == "https://arxiv.org/pdf/1706.03762v5"


@pytest.mark.asyncio
async def test_search_openalex_parses_response():
    from app.search_service import _search_openalex

    mock_response = {
        "meta": {"count": 1, "page": 1, "per_page": 25},
        "results": [
            {
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper on Deep Learning",
                "display_name": "Test Paper on Deep Learning",
                "relevance_score": 42.5,
                "publication_year": 2023,
                "publication_date": "2023-06-15",
                "cited_by_count": 150,
                "authorships": [
                    {"author": {"display_name": "Chen, W."}, "author_position": "first"},
                    {"author": {"display_name": "Davis, M."}, "author_position": "last"},
                ],
                "abstract_inverted_index": {
                    "Deep": [0], "learning": [1], "has": [2],
                    "transformed": [3], "AI": [4], "research": [5],
                },
                "primary_location": {
                    "source": {"display_name": "Nature Machine Intelligence"},
                    "landing_page_url": "https://nature.com/articles/test",
                    "pdf_url": "https://nature.com/articles/test.pdf",
                },
                "open_access": {"is_oa": True, "oa_url": "https://nature.com/articles/test.pdf"},
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = await _search_openalex(
            "deep learning", {"api_key": "test_key"}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper on Deep Learning"
    assert r.is_academic is True
    assert r.authors == ["Chen, W.", "Davis, M."]
    assert r.year == 2023
    assert r.venue == "Nature Machine Intelligence"
    assert r.citation_count == 150
    assert r.doi == "10.1234/test"
    assert r.pdf_url == "https://nature.com/articles/test.pdf"
    assert r.content == "Deep learning has transformed AI research"


@pytest.mark.asyncio
async def test_academic_search_all_providers():
    from app.search_service import academic_search

    s2_result = SearchResult(
        title="S2 Paper", url="s2.com", content="S2 abstract",
        doi="10.1/s2", is_academic=True, authors=["A"], year=2023,
        venue="NeurIPS", citation_count=50,
    )
    arxiv_result = SearchResult(
        title="arXiv Paper", url="arxiv.org", content="arXiv abstract",
        doi=None, is_academic=True, authors=["B"], year=2023,
    )
    openalex_result = SearchResult(
        title="OA Paper", url="openalex.org", content="OA abstract",
        doi="10.1/oa", is_academic=True, authors=["C"], year=2023,
        citation_count=30,
    )

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[s2_result]) as mock_s2, \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock, return_value=[arxiv_result]) as mock_arxiv, \
         patch("app.search_service._search_openalex", new_callable=AsyncMock, return_value=[openalex_result]) as mock_oa:

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}, "arxiv": {}, "openalex": {"api_key": "k"}},
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
            max_results=5,
        )

    assert len(results) == 3
    assert all(r.is_academic for r in results)


@pytest.mark.asyncio
async def test_academic_search_deduplicates():
    from app.search_service import academic_search

    paper = SearchResult(
        title="Same Paper", url="s2.com", content="Abstract",
        doi="10.1/same", is_academic=True, authors=["A"], year=2023,
        citation_count=50,
    )
    paper_dup = SearchResult(
        title="Same Paper", url="oa.org", content="Abstract",
        doi="10.1/same", is_academic=True, authors=["A"], year=2023,
    )

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[paper]), \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock, return_value=[]), \
         patch("app.search_service._search_openalex", new_callable=AsyncMock, return_value=[paper_dup]):

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}, "openalex": {"api_key": "k"}},
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    assert results[0].citation_count == 50


@pytest.mark.asyncio
async def test_academic_search_skips_missing_providers():
    from app.search_service import academic_search

    paper = SearchResult(title="P", url="u", content="c", is_academic=True)

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[paper]), \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock) as mock_arxiv, \
         patch("app.search_service._search_openalex", new_callable=AsyncMock) as mock_oa:

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}},
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    mock_arxiv.assert_not_called()
    mock_oa.assert_not_called()


# ---------------------------------------------------------------------------
# Task 1: pdf_url field tests
# ---------------------------------------------------------------------------


def test_search_result_pdf_url_default():
    r = SearchResult(title="T", url="u", content="c")
    assert r.pdf_url is None


def test_search_result_pdf_url_populated():
    r = SearchResult(title="T", url="u", content="c", pdf_url="https://arxiv.org/pdf/1234.pdf")
    assert r.pdf_url == "https://arxiv.org/pdf/1234.pdf"
