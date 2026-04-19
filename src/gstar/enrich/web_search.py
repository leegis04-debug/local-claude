"""Web search 어댑터 — Brave / SerpAPI / Firecrawl / Mock."""

from __future__ import annotations

import asyncio
import os
from typing import Any


async def web_search(
    query: str,
    *,
    backend: str = "mock",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """쿼리 → [{"title", "url", "snippet", "content"}, ...]"""
    backend = (backend or "mock").lower()
    if backend == "mock":
        return _mock_results(query, top_k)
    if backend == "brave":
        return await _brave_search(query, top_k)
    if backend == "serpapi":
        return await _serpapi_search(query, top_k)
    if backend == "firecrawl":
        return await _firecrawl_search(query, top_k)
    return _mock_results(query, top_k)


def _mock_results(query: str, top_k: int) -> list[dict[str, Any]]:
    """테스트·오프라인용. 결정론적 가짜 결과."""
    base = [
        {
            "title": f"{query} — 관련 자료 #{i+1}",
            "url": f"https://example.com/search?q={query}&r={i+1}",
            "snippet": f"{query} 에 대한 설명 및 통계 요약.",
            "content": (
                f"{query} 와 관련된 구체 수치와 사례: 해당 주제는 최근 3년간 연평균 "
                f"성장률 12%를 기록하고 있으며 주요 지표가 개선 추세. 샘플 근거."
            ),
        }
        for i in range(top_k)
    ]
    return base


async def _brave_search(query: str, top_k: int) -> list[dict[str, Any]]:
    import httpx

    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": top_k},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        results = data.get("web", {}).get("results", [])[:top_k]
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
                "content": r.get("description", ""),
            }
            for r in results
        ]


async def _serpapi_search(query: str, top_k: int) -> list[dict[str, Any]]:
    import httpx

    key = os.environ.get("SERPAPI_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            "https://serpapi.com/search.json",
            params={"q": query, "api_key": key, "num": top_k, "hl": "ko", "gl": "kr"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        organic = data.get("organic_results", [])[:top_k]
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "content": r.get("snippet", ""),
            }
            for r in organic
        ]


async def _firecrawl_search(query: str, top_k: int) -> list[dict[str, Any]]:
    import httpx

    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.firecrawl.dev/v1/search",
            json={"query": query, "limit": top_k},
            headers={"Authorization": f"Bearer {key}"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        results = (data.get("data") or [])[:top_k]
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", r.get("snippet", "")),
                "content": r.get("content") or r.get("description", ""),
            }
            for r in results
        ]
