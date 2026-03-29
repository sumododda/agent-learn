from app.search_service import SearchResult


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
