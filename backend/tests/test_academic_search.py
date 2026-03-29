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
