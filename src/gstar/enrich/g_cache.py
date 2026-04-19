"""Web-to-G 자동 캐시.

동작:
1. 쿼리를 먼저 G 에서 FAISS 임베딩 검색 (기본 전역, 누적된 타 프로젝트 fact 재사용).
2. G 유사 결과가 min_hits 이상 + 점수가 min_score 이상이면 web skip (cache hit).
3. 아니면 web_search 호출 → (옵션) web_fetch 본문 보강 → md 저장 → ingest_path 로 G 역삽입.

정보량이 쌓일수록 cache hit 비율이 올라가 실제 유료 검색 호출은 감소.

Env toggles:
- GP_WEB_CACHE_FIRST=on|off   (기본 on) — G 선조회
- GP_WEB_AUTO_INGEST=on|off   (기본 on) — 웹 결과 자동 역삽입
- GP_WEB_MIN_HITS=3           — G 최소 건수 (이 개수 이상이면 web skip)
- GP_WEB_MIN_SCORE=0.55       — SBERT 코사인 유사도 임계 (실측 0.7+ 관련, 0.5 애매, 0.3 미만 무관)
- GP_WEB_NAMESPACE=web_cache  — 역삽입 기본 namespace
- GP_WEB_SEARCH_NS=*          — 조회 대상 namespace. `*` 전역(기본), 특정 값 시 해당 ns 만
- GP_WEB_FETCH_FULL=on|off    (기본 on) — snippet 만 쓰지 말고 web_fetch 본문 보강
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gstar.enrich.web_search import web_fetch, web_search


@dataclass
class CacheSearchResult:
    query: str
    backend: str | None
    from_cache: bool
    g_hits: int
    web_hits: int
    results: list[dict[str, Any]]
    ingested: int = 0
    ingest_ns: str = ""
    skipped_reason: str = ""
    took_ms: int = 0


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "on", "true", "yes", "y")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


async def _g_precheck(
    query: str, search_ns: str, min_score: float, top_k: int
) -> list[dict]:
    """G 에서 FAISS 임베딩 기반 semantic search. 반환은 score 내림차순.

    `search_ns` 가 `*` 또는 빈 값이면 전역 검색. 누적된 타 프로젝트 자료도 재사용.
    """
    try:
        from gstar.config import Paths
        from gstar.embedding.sbert import SBertEmbedder
        from gstar.storage.duckdb_store import DuckStore
        from gstar.storage.faiss_index import FaissStore
    except ImportError:
        return []
    paths = Paths.load()
    if not paths.db.exists():
        return []
    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        emb = embedder.encode([query])[0]
        raw = faiss.search(emb, k=max(top_k * 6, 30))
        hits: list[dict] = []
        seen_texts: set[str] = set()
        filter_ns = None if search_ns in ("", "*", "all") else search_ns
        for nid, score in raw:
            if score < min_score:
                continue
            n = store.get_node(nid)
            if n is None or n.kind != "fact":
                continue
            if filter_ns and n.source_namespace != filter_ns:
                continue
            sig = n.text[:120]
            if sig in seen_texts:
                continue
            seen_texts.add(sig)
            attrs = n.attrs or {}
            hits.append(
                {
                    "title": (n.text[:80] + "…") if len(n.text) > 80 else n.text,
                    "url": attrs.get("source_url", f"gstar://node/{n.id}"),
                    "snippet": n.text[:400],
                    "content": n.text,
                    "score": float(score),
                    "_source": "g_cache",
                    "_namespace": n.source_namespace,
                    "node_id": n.id,
                }
            )
            if len(hits) >= top_k * 3:
                break
        return hits
    finally:
        store.close()


async def _ingest_web_results(
    query: str,
    results: list[dict],
    namespace: str,
    *,
    fetch_full: bool,
) -> int:
    """웹 결과를 md 로 저장하고 ingest_path 호출. 반환: 삽입된 fact 수."""
    if not results:
        return 0
    from gstar.config import Paths
    from gstar.embedding.sbert import SBertEmbedder
    from gstar.ingest.pipeline import ingest_path
    from gstar.storage.duckdb_store import DuckStore
    from gstar.storage.faiss_index import FaissStore

    query_slug = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    cache_dir = Path.home() / ".gstar" / "web_cache" / query_slug
    cache_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        uid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        md_path = cache_dir / f"{uid}.md"
        if md_path.exists():
            continue
        body = r.get("content") or r.get("snippet") or ""
        if fetch_full and len(body) < 1500 and url.startswith("http"):
            try:
                fetched = await web_fetch(url, max_chars=20_000)
                if fetched and len(fetched) > len(body):
                    body = fetched
            except Exception:
                pass
        md = (
            f"<!-- source_url: {url} -->\n"
            f"<!-- query: {query} -->\n"
            f"<!-- fetched_at: {int(time.time())} -->\n"
            f"# {r.get('title', '')}\n\n"
            f"URL: {url}\n\n"
            f"> {r.get('snippet', '')}\n\n"
            f"{body}\n"
        )
        md_path.write_text(md, encoding="utf-8")

    paths = Paths.load()
    if not paths.db.exists():
        return 0
    embedder = SBertEmbedder()
    faiss = FaissStore(paths.faiss, dim=embedder.dim)
    store = DuckStore(paths.db)
    try:
        report = ingest_path(
            cache_dir,
            store=store,
            faiss=faiss,
            embedder=embedder,
            namespace=namespace,
            track="document",
        )
        faiss.save()
        return report.facts
    finally:
        store.close()


async def cache_first_web_search(
    query: str,
    *,
    backend: str = "brave",
    top_k: int = 5,
    auto_ingest: bool | None = None,
    cache_first: bool | None = None,
    namespace: str | None = None,
    min_hits: int | None = None,
    min_score: float | None = None,
    fetch_full: bool | None = None,
) -> CacheSearchResult:
    """cache-first 웹 검색. G 에 충분한 정보가 있으면 web skip, 없으면 검색+자동 역삽입."""
    t0 = time.time()
    if cache_first is None:
        cache_first = _env_bool("GP_WEB_CACHE_FIRST", True)
    if auto_ingest is None:
        auto_ingest = _env_bool("GP_WEB_AUTO_INGEST", True)
    if fetch_full is None:
        fetch_full = _env_bool("GP_WEB_FETCH_FULL", True)
    namespace = namespace or os.environ.get("GP_WEB_NAMESPACE", "web_cache")
    search_ns = os.environ.get("GP_WEB_SEARCH_NS", "*")
    min_hits = _env_int("GP_WEB_MIN_HITS", 3) if min_hits is None else min_hits
    min_score = _env_float("GP_WEB_MIN_SCORE", 0.55) if min_score is None else min_score

    g_hits: list[dict] = []
    if cache_first:
        try:
            g_hits = await _g_precheck(query, search_ns, min_score, top_k)
        except Exception:
            g_hits = []

    if cache_first and len(g_hits) >= min_hits:
        return CacheSearchResult(
            query=query,
            backend=None,
            from_cache=True,
            g_hits=len(g_hits),
            web_hits=0,
            results=g_hits[:top_k],
            skipped_reason=f"G cache sufficient ({len(g_hits)}>={min_hits})",
            ingest_ns=namespace,
            took_ms=int((time.time() - t0) * 1000),
        )

    web_results = await web_search(query, backend=backend, top_k=top_k)

    ingested = 0
    if auto_ingest and web_results:
        try:
            ingested = await _ingest_web_results(
                query, web_results, namespace, fetch_full=fetch_full
            )
        except Exception:
            ingested = 0

    return CacheSearchResult(
        query=query,
        backend=backend,
        from_cache=False,
        g_hits=len(g_hits),
        web_hits=len(web_results),
        results=web_results,
        ingested=ingested,
        ingest_ns=namespace,
        took_ms=int((time.time() - t0) * 1000),
    )
