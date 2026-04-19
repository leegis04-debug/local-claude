"""Web search 어댑터 — 12종 백엔드. 진짜 무료: tavily/exa/ddg/wiki/playwright."""

from __future__ import annotations

import asyncio
import os
from typing import Any

# 모듈 임포트 시 ~/.gstar/keys.env 자동 로드 (shell env 가 우선)
try:
    from gstar.enrich.keys import load_keys as _load_keys

    _load_keys()
except Exception:
    pass


async def web_search(
    query: str,
    *,
    backend: str = "ddg",
    top_k: int = 3,
    track_quota: bool = True,
) -> list[dict[str, Any]]:
    """쿼리 → [{"title", "url", "snippet", "content"}, ...]

    기본 백엔드 `ddg` — DuckDuckGo 무료·무키. API 키 불필요.
    `track_quota=True` (기본) 면 호출 성공 시 quota 기록.
    `backend="auto"` 면 QuotaManager 가 무료 한도 남은 백엔드 자동 선택.
    """
    backend = (backend or "ddg").lower()

    if backend == "auto":
        from gstar.enrich.quota import QuotaManager

        qm = QuotaManager()
        preferred = os.environ.get("GP_ENRICH_PREFERRED", "tavily,brave,exa,searchapi,you,ddg").split(",")
        picked = qm.pick_backend([b.strip() for b in preferred])
        if not picked:
            return []
        backend = picked
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
    if backend == "tavily":
        return await _tavily_search(query, top_k)
    if backend == "exa":
        return await _exa_search(query, top_k)
    if backend == "searchapi":
        return await _searchapi_search(query, top_k)
    if backend == "perplexity":
        return await _perplexity_search(query, top_k)
    if backend == "you" or backend == "you.com":
        return await _you_search(query, top_k)
    if backend == "self_made" or backend == "self":
        from gstar.enrich.self_made import self_made_search

        hits = await self_made_search(query, top_k=top_k)
        return [h.to_dict() for h in hits]
    if backend == "claude_style" or backend == "search_and_fetch":
        from gstar.enrich.self_made import search_and_fetch

        hits = await search_and_fetch(query, top_k=top_k)
        return [h.to_dict() for h in hits]
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


async def _exa_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Exa (구 Metaphor) — AI 검색, 월 1,000 요청 무료. EXA_API_KEY."""
    import httpx

    key = os.environ.get("EXA_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json={
                    "query": query,
                    "numResults": top_k,
                    "contents": {"text": True, "highlights": True},
                    "type": "neural",
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []
    out = []
    for item in (data.get("results") or [])[:top_k]:
        text = item.get("text") or ""
        if not text:
            h = item.get("highlights") or []
            text = " ".join(h) if h else item.get("snippet", "")
        out.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": text[:400],
            "content": text[:8000],
        })
    return out


async def _searchapi_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """SearchApi.io — Google 결과. 월 100 req 무료. SEARCHAPI_KEY."""
    import httpx

    key = os.environ.get("SEARCHAPI_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://www.searchapi.io/api/v1/search",
                params={
                    "engine": "google",
                    "q": query,
                    "num": top_k,
                    "hl": "ko",
                    "gl": "kr",
                    "api_key": key,
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []
    organic = (data.get("organic_results") or [])[:top_k]
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "snippet": r.get("snippet", ""),
            "content": r.get("snippet", ""),
        }
        for r in organic
    ]


async def _perplexity_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Perplexity — AI answer + 출처. PERPLEXITY_API_KEY. 유료($5 free credit)."""
    import httpx

    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "sonar-small-online",
                    "messages": [{"role": "user", "content": query}],
                    "return_citations": True,
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []
    answer = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    citations = data.get("citations") or []
    out: list[dict[str, Any]] = []
    if answer and citations:
        for i, url in enumerate(citations[:top_k]):
            out.append({
                "title": f"Perplexity citation #{i+1}",
                "url": url,
                "snippet": answer[:400],
                "content": answer[:4000],
            })
    return out


async def _you_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """You.com — 무료 티어 제공, YOU_API_KEY."""
    import httpx

    key = os.environ.get("YOU_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://api.ydc-index.io/search",
                headers={"X-API-Key": key},
                params={"query": query, "num_web_results": top_k},
            )
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []
    hits = data.get("hits") or []
    return [
        {
            "title": h.get("title", ""),
            "url": h.get("url", ""),
            "snippet": (h.get("description") or " ".join(h.get("snippets") or []))[:400],
            "content": " ".join(h.get("snippets") or [])[:4000],
        }
        for h in hits[:top_k]
    ]


async def _tavily_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Tavily — AI-최적 검색. 검색 결과에 raw_content 포함 (fetch 생략 가능)."""
    import httpx

    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    body = {
        "api_key": key,
        "query": query,
        "search_depth": "advanced",
        "max_results": top_k,
        "include_raw_content": True,
        "include_answer": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post("https://api.tavily.com/search", json=body)
        if r.status_code != 200:
            return []
        data = r.json()
        results = (data.get("results") or [])[:top_k]
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "content": r.get("raw_content") or r.get("content", ""),
            }
            for r in results
        ]


async def web_fetch(url: str, *, timeout_s: float = 20.0, max_chars: int = 20_000) -> str:
    """Claude WebFetch 대체. URL → 본문 텍스트.

    1차: httpx 간단 GET + BeautifulSoup (JS 없는 페이지)
    2차: Playwright 렌더 (JS 필요 페이지)
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    async with httpx.AsyncClient(
        timeout=timeout_s,
        headers={"User-Agent": "Mozilla/5.0 local-claude/enrich"},
        follow_redirects=True,
    ) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            if text and len(text) > 200:
                return text[:max_chars]
        except Exception:
            pass

    try:
        html = await _playwright_fetch_html(url, timeout_ms=int(timeout_s * 1000))
        if not html:
            return ""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)[:max_chars]
    except Exception:
        return ""


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
