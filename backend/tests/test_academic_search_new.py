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
