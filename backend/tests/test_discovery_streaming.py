import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.agent import TopicBrief
from app.search_service import SearchResult


@pytest.mark.asyncio
async def test_discover_topic_streams_web_and_academic_events_in_completion_order():
    from app.agent_service import discover_topic

    emitted: list[tuple[str, dict]] = []

    async def on_event(event_type: str, data: dict) -> None:
        emitted.append((event_type, data))

    async def mock_web_search(
        provider: str,
        query: str,
        credentials: dict,
        user_id: str = "",
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> list[SearchResult]:
        await asyncio.sleep(0.03 if query == "slow query" else 0.01)
        return [
            SearchResult(
                title=f"Web result for {query}",
                url=f"https://example.com/{query.replace(' ', '-')}",
                content=f"Snippet for {query}",
                score=0.8,
            )
        ]

    async def mock_academic_search(
        query: str,
        academic_credentials: dict[str, dict],
        academic_options: dict,
        max_results: int = 5,
        timeout_seconds: float | None = None,
    ) -> list[SearchResult]:
        await asyncio.sleep(0.015 if query == "slow query" else 0.005)
        return [
            SearchResult(
                title=f"Paper for {query}",
                url=f"https://arxiv.org/abs/{query.replace(' ', '-')}",
                content=f"Abstract for {query}",
                score=0.0,
                authors=["Ada Lovelace"],
                year=2024,
                venue="NeurIPS",
                citation_count=42,
                doi=f"10.1234/{query.replace(' ', '-')}",
                is_academic=True,
            )
        ]

    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {
        "structured_response": TopicBrief(
            key_concepts=["Agents"],
            subtopics=["Security"],
            authoritative_sources=["https://example.com"],
            learning_progression="intro -> advanced",
            open_debates=[],
            raw_search_results=[],
        ),
        "messages": [],
    }

    with (
        patch("app.agent_service._generate_discovery_queries", new_callable=AsyncMock, return_value=["slow query", "fast query"]),
        patch("app.search_service.search_with_fallback", new_callable=AsyncMock, side_effect=mock_web_search),
        patch("app.search_service.academic_search", new_callable=AsyncMock, side_effect=mock_academic_search),
        patch("app.agent_service.create_discovery_researcher", return_value=mock_agent),
    ):
        result = await discover_topic(
            "AI Agents Security",
            provider="anthropic",
            model="claude-test",
            credentials={"api_key": "test"},
            search_provider="duckduckgo",
            search_credentials={},
            on_event=on_event,
            user_id="user-1",
            academic_credentials={"arxiv": {}},
            academic_options={"enabled": True, "year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert result.key_concepts == ["Agents"]

    event_types = [event_type for event_type, _ in emitted]
    assert "query" in event_types
    assert "source" in event_types
    assert "query_done" in event_types
    assert "academic_query" in event_types
    assert "academic_source" in event_types
    assert "academic_query_done" in event_types
    assert "synthesizing" in event_types
    assert "synthesis_done" in event_types

    slow_web_done = next(
        idx for idx, (event_type, data) in enumerate(emitted)
        if event_type == "query_done" and data.get("index") == 0
    )
    fast_academic_done = next(
        idx for idx, (event_type, data) in enumerate(emitted)
        if event_type == "academic_query_done" and data.get("index") == 1
    )

    assert fast_academic_done < slow_web_done
