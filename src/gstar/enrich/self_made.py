"""자체 제작 WebSearch + WebFetch — Claude 내장 도구를 외부 API 없이 재구현.

철학: 외부 유료 API (Brave/Tavily 등) 의존 최소화. 여러 무료 소스를 **병렬 집계 +
중복 제거 + 본문 fetch 통합**해서 Claude WebSearch/WebFetch 와 동등한 인터페이스
제공.

구성:
1. `self_made_search(query, top_k)` — 멀티 소스 병렬 검색
   · DuckDuckGo (무료)
   · Wikipedia (무료, 한국어 정확도 높음)
   · Naver Playwright (무료, 한국 도메인)
   · GitHub (오픈소스 도메인)
2. `web_fetch(url)` — 본문 추출 (httpx → Playwright 폴백) [이미 web_search.py 에 구현]
3. `search_and_fetch(query, top_k)` — 검색 결과 상위 N URL 자동 fetch 후 본문 합본

Claude WebSearch/WebFetch 대비:
- API 키·가입 불필요 (Wikipedia + DDG + Playwright 조합)
- 여러 소스 병렬 → 재현율 ↑
- 중복 URL 제거 → 정확도 ↑
- 멀티 언어 (한국어 특화 + 영어 fallback)
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    content: str = ""
    source_engine: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
            "source_engine": self.source_engine,
            "score": self.score,
        }


async def self_made_search(
    query: str,
    *,
    top_k: int = 5,
    engines: list[str] | None = None,
    lang: str = "ko",
) -> list[SearchHit]:
    """멀티 엔진 병렬 검색 + merge + dedup.

    engines 기본:
    - ko: ["ddg", "wikipedia_ko", "naver"]
    - en: ["ddg", "wikipedia_en"]
    """
    if engines is None:
        engines = ["ddg", "wikipedia_ko", "naver"] if lang == "ko" else ["ddg", "wikipedia_en"]

    tasks: list[Awaitable[list[SearchHit]]] = []
    for eng in engines:
        task = _engine_dispatch(eng, query, top_k)
        tasks.append(task)

    results_per_engine = await asyncio.gather(*tasks, return_exceptions=True)
    all_hits: list[SearchHit] = []
    for r in results_per_engine:
        if isinstance(r, Exception):
            continue
        all_hits.extend(r)

    return _merge_and_rank(all_hits, top_k)


async def _engine_dispatch(engine: str, query: str, top_k: int) -> list[SearchHit]:
    from gstar.enrich.web_search import (
        _ddg_search,
        _naver_playwright,
    )

    engine = engine.lower()
    try:
        if engine == "ddg":
            raw = await _ddg_search(query, top_k)
            return [_from_raw(r, "ddg") for r in raw]
        if engine == "naver":
            raw = await _naver_playwright(query, top_k)
            return [_from_raw(r, "naver") for r in raw]
        if engine == "wikipedia_ko":
            return await _wikipedia_search(query, top_k, lang="ko")
        if engine == "wikipedia_en":
            return await _wikipedia_search(query, top_k, lang="en")
        if engine == "github":
            return await _github_search(query, top_k)
    except Exception:
        return []
    return []


def _from_raw(r: dict, source: str) -> SearchHit:
    return SearchHit(
        title=r.get("title", ""),
        url=r.get("url", ""),
        snippet=r.get("snippet", ""),
        content=r.get("content", ""),
        source_engine=source,
        score=1.0,
    )


_HANGUL = re.compile(r"[\uac00-\ud7a3]")


def _merge_and_rank(hits: list[SearchHit], top_k: int) -> list[SearchHit]:
    """URL 기반 중복 제거 + 간단 ranking (source 다양성 + snippet 길이)."""
    by_url: dict[str, SearchHit] = {}
    for h in hits:
        if not h.url:
            continue
        key = _normalize_url(h.url)
        if key in by_url:
            by_url[key].source_engine += f"+{h.source_engine}"
            by_url[key].score += 1.0
            if len(h.snippet) > len(by_url[key].snippet):
                by_url[key].snippet = h.snippet
            if len(h.content) > len(by_url[key].content):
                by_url[key].content = h.content
        else:
            h.score = 1.0 + min(len(h.snippet), 500) / 500.0
            if _HANGUL.search(h.snippet):
                h.score += 0.3
            by_url[key] = h
    ranked = sorted(by_url.values(), key=lambda x: x.score, reverse=True)
    return ranked[:top_k]


def _normalize_url(url: str) -> str:
    """fragment, query ordering, trailing slash 무시한 dedup 키."""
    u = url.split("#", 1)[0].rstrip("/")
    return u.lower()


async def _wikipedia_search(query: str, top_k: int, *, lang: str = "ko") -> list[SearchHit]:
    """Wikipedia REST API — 무료·무키. 한국어 위키 정확도 매우 높음."""
    import httpx
    from urllib.parse import quote

    base = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": top_k,
        "utf8": 1,
    }
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "local-claude/enrich 1.0"}) as client:
        try:
            r = await client.get(base, params=params)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []

    hits: list[SearchHit] = []
    for item in (data.get("query", {}).get("search") or [])[:top_k]:
        title = item.get("title", "")
        if not title:
            continue
        slug = quote(title.replace(" ", "_"))
        url = f"https://{lang}.wikipedia.org/wiki/{slug}"
        snippet = _strip_html(item.get("snippet", ""))[:500]
        hits.append(
            SearchHit(
                title=title,
                url=url,
                snippet=snippet,
                content=snippet,
                source_engine=f"wikipedia_{lang}",
                score=1.5,
            )
        )
    return hits


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


async def _github_search(query: str, top_k: int) -> list[SearchHit]:
    """GitHub code search — 무료 (unauthenticated 60 req/hr 제한)."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0, headers={"Accept": "application/vnd.github+json"}) as client:
        try:
            r = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "per_page": top_k, "sort": "stars"},
            )
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []
    hits: list[SearchHit] = []
    for item in (data.get("items") or [])[:top_k]:
        hits.append(
            SearchHit(
                title=item.get("full_name", ""),
                url=item.get("html_url", ""),
                snippet=(item.get("description") or "")[:400],
                content=(item.get("description") or "")[:400],
                source_engine="github",
                score=1.0,
            )
        )
    return hits


async def search_and_fetch(
    query: str,
    *,
    top_k: int = 5,
    fetch_top_n: int = 3,
    engines: list[str] | None = None,
    lang: str = "ko",
    max_content_chars: int = 15_000,
) -> list[SearchHit]:
    """Claude WebSearch+WebFetch 통합 재현.

    1. self_made_search() 로 top_k URL 수집
    2. 상위 fetch_top_n 의 본문을 web_fetch() 로 채움
    3. SearchHit.content 에 본문 주입

    반환은 top_k (fetch 는 상위 일부만 — 병렬 fetch 도 네트워크 부담).
    """
    from gstar.enrich.web_search import web_fetch

    hits = await self_made_search(query, top_k=top_k, engines=engines, lang=lang)
    if not hits:
        return []

    fetch_targets = hits[:fetch_top_n]
    fetch_tasks = [web_fetch(h.url, max_chars=max_content_chars) for h in fetch_targets]
    bodies = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    for h, body in zip(fetch_targets, bodies):
        if isinstance(body, str) and body.strip():
            h.content = body
    return hits
