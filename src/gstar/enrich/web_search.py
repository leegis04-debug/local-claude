"""Web search 어댑터 — Brave / SerpAPI / Firecrawl / Mock."""

from __future__ import annotations

import asyncio
import os
from typing import Any


async def web_search(
    query: str,
    *,
    backend: str = "ddg",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """쿼리 → [{"title", "url", "snippet", "content"}, ...]

    기본 백엔드 `ddg` — DuckDuckGo 무료·무키. API 키 불필요.
    """
    backend = (backend or "ddg").lower()
    if backend == "mock":
        return _mock_results(query, top_k)
    if backend == "ddg" or backend == "duckduckgo":
        return await _ddg_search(query, top_k)
    if backend == "brave":
        return await _brave_search(query, top_k)
    if backend == "serpapi":
        return await _serpapi_search(query, top_k)
    if backend == "firecrawl":
        return await _firecrawl_search(query, top_k)
    if backend == "naver" or backend == "playwright_naver":
        return await _naver_playwright(query, top_k)
    if backend == "google" or backend == "playwright_google":
        return await _google_playwright(query, top_k)
    return _mock_results(query, top_k)


async def _playwright_fetch_html(url: str, wait_selector: str | None = None, timeout_ms: int = 20000) -> str:
    """Playwright sync 를 asyncio thread 에서 실행."""
    def _run() -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                    locale="ko-KR",
                    viewport={"width": 1280, "height": 900},
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:
                    pass
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=timeout_ms)
                    except Exception:
                        pass
                return page.content()
            finally:
                browser.close()
    return await asyncio.to_thread(_run)


async def _naver_playwright(query: str, top_k: int) -> list[dict[str, Any]]:
    """네이버 통합검색 — 한국어 품질 최상, 키 불필요.

    웹 탭으로 직접 이동 (통합 탭은 카드·메뉴 혼재). 외부 http 링크만 결과로 인정.
    """
    from urllib.parse import quote

    url = f"https://search.naver.com/search.naver?where=web&query={quote(query)}"
    html = await _playwright_fetch_html(url, wait_selector="body", timeout_ms=20000)
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in soup.select("div.total_wrap, li.bx_wrap, li.bx"):
        if item.find_parent(class_=["option_list", "lnb", "tab_wrap", "category_tab"]):
            continue
        a = item.select_one("a.link_tit, a.total_tit, a.api_txt_lines.total_tit")
        if not a:
            a = item.select_one("a[href^='http']")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        link = a.get("href", "")
        if not title or not link.startswith("http"):
            continue
        if link in seen_urls:
            continue
        seen_urls.add(link)
        snippet_el = item.select_one("div.total_dsc, div.api_txt_lines.total_dsc, .dsc_txt, .dsc_area")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"title": title[:200], "url": link, "snippet": snippet[:400], "content": snippet[:400]})
        if len(results) >= top_k:
            break
    return results


async def _google_playwright(query: str, top_k: int) -> list[dict[str, Any]]:
    """구글 검색 — JS 렌더링 필수, reCAPTCHA 가끔 뜸."""
    from urllib.parse import quote

    url = f"https://www.google.com/search?q={quote(query)}&hl=ko&gl=kr"
    html = await _playwright_fetch_html(url, wait_selector="div#search", timeout_ms=20000)
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for item in soup.select("div.g, div[data-hveid]")[: top_k * 3]:
        a = item.select_one("a")
        h3 = item.select_one("h3")
        if not a or not h3:
            continue
        title = h3.get_text(" ", strip=True)
        link = a.get("href", "")
        if not title or not link.startswith("http"):
            continue
        snip_el = item.select_one("div[data-sncf], span[data-snf]")
        snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
        results.append({"title": title[:200], "url": link, "snippet": snippet[:400], "content": snippet[:400]})
        if len(results) >= top_k:
            break
    return results


async def _ddg_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """DuckDuckGo — duckduckgo-search 라이브러리, API 키 불필요."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=top_k, region="kr-kr"))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", r.get("url", "")),
                "snippet": r.get("body", ""),
                "content": r.get("body", ""),
            }
            for r in results
        ]
    except Exception:
        return []


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
