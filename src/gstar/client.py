"""G HTTP 클라이언트 — 맥북 등 원격에서 미니 PC g-serve 호출.

KnowledgeStore Protocol 의 서브셋을 HTTP 로 구현. 로컬 DuckStore 드롭인 아님 —
필요한 메서드(search/ingest/goal/verify) 중심.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class Hit:
    text: str
    score: float
    source: str
    node_id: str
    content_hash: str
    namespace: str


class GClient:
    """httpx 기반 thin wrapper. env `GSTAR_SERVER_URL` 우선, 기본 미니 PC."""

    def __init__(self, base_url: str = "http://100.79.251.53:9999", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def health(self, *, timeout: float | None = 2.0) -> dict:
        """서버 가용성 ping. auto 폴백 감지용 — 짧은 timeout 권장."""
        kwargs: dict = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self.http.get("/health", **kwargs).raise_for_status().json()

    def search(self, query: str, top_k: int = 5, namespace: str | None = None) -> list[Hit]:
        body: dict = {"query": query, "top_k": top_k}
        if namespace:
            body["namespace"] = namespace
        r = self.http.post("/search/hybrid", json=body)
        r.raise_for_status()
        return [Hit(**h) for h in r.json()]

    def get_node(self, node_id: str) -> dict | None:
        r = self.http.get(f"/nodes/{node_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def list_nodes(self, kind: str | None = None, namespace: str | None = None, limit: int = 100):
        params: dict = {"limit": limit}
        if kind:
            params["kind"] = kind
        if namespace:
            params["namespace"] = namespace
        r = self.http.get("/nodes", params=params)
        r.raise_for_status()
        return r.json()

    def create_goal(self, text: str, kind: str = "proposal") -> dict:
        r = self.http.post("/goals", json={"text": text, "kind": kind})
        r.raise_for_status()
        return r.json()

    def verify_chain(self, namespace: str) -> dict:
        r = self.http.get("/verify/chain", params={"ns": namespace})
        r.raise_for_status()
        return r.json()

    def ingest_web(
        self,
        query: str,
        results: list[dict],
        *,
        namespace: str = "web_cache",
        ttl_days: int = 30,
        timeout: float | None = 60.0,
    ) -> dict:
        """Web→G 자동 캐시 bulk ingest. cache_first_web_search 원격 경로가 호출.

        `results` 는 `[{"url","title","snippet","content"}]`. content 는 맥북이
        `web_fetch` 로 완성한 본문 — 서버는 fetch 하지 않음. 반환: facts/entities/
        edges/skipped_dedupe.
        """
        body = {
            "query": query,
            "namespace": namespace,
            "ttl_days": ttl_days,
            "results": [
                {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "content": r.get("content") or r.get("snippet", ""),
                }
                for r in results
            ],
        }
        kwargs: dict = {"json": body}
        if timeout is not None:
            kwargs["timeout"] = timeout
        r = self.http.post("/ingest/web", **kwargs)
        r.raise_for_status()
        return r.json()

    # ---------- Phase A: entity ----------

    def entity_neighbors(
        self,
        entity_id: str,
        *,
        kind: str | None = None,
        relation_types: list[str] | None = None,
        max_hops: int = 1,
    ) -> dict:
        params: dict = {"max_hops": max_hops}
        if kind:
            params["kind"] = kind
        if relation_types:
            params["relation_type"] = relation_types
        r = self.http.get(f"/entities/{entity_id}/neighbors", params=params)
        r.raise_for_status()
        return r.json()

    def entity_trace_chain(
        self,
        start: str,
        target_kind: str,
        *,
        max_depth: int = 4,
        relation_types: list[str] | None = None,
    ) -> dict:
        params: dict = {"start": start, "target_kind": target_kind, "max_depth": max_depth}
        if relation_types:
            params["relation_type"] = relation_types
        r = self.http.get("/entities/chain", params=params)
        r.raise_for_status()
        return r.json()

    def entity_stats(self, project_id: str, track: str | None = None) -> dict:
        params: dict = {"project_id": project_id}
        if track:
            params["track"] = track
        r = self.http.get("/entities/stats", params=params)
        r.raise_for_status()
        return r.json()

    def list_entities(
        self,
        project_id: str,
        *,
        track: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict = {"project_id": project_id, "limit": limit}
        if track:
            params["track"] = track
        if kind:
            params["kind"] = kind
        r = self.http.get("/entities", params=params)
        r.raise_for_status()
        return r.json()

    # ---------- Phase D: fused wrapper + notes + worker + communities ----------

    def fused_search(
        self,
        query: str,
        *,
        top_k: int = 10,
        namespace: str | None = None,
        use_gateway: bool = True,
        use_g: bool = True,
        gateway_timeout: float = 3.0,
    ) -> list[dict]:
        """G 상위 wrapper. G 내부 + Gateway(Qdrant+Neo4j) 결과 병합.

        반환 아이템에 `_source ∈ {g, gateway_qdrant, gateway_neo4j}` 태그.
        """
        body: dict = {
            "query": query,
            "top_k": top_k,
            "use_gateway": use_gateway,
            "use_g": use_g,
            "gateway_timeout": gateway_timeout,
        }
        if namespace:
            body["namespace"] = namespace
        r = self.http.post("/search/fused", json=body, timeout=gateway_timeout * 4 + 5)
        r.raise_for_status()
        return r.json()

    def notes_add(
        self,
        text: str,
        *,
        source: str = "manual",
        tags: list[str] | None = None,
        namespace: str = "personal_notes",
        track: str = "document",
    ) -> dict:
        body: dict = {
            "text": text,
            "source": source,
            "tags": tags or [],
            "namespace": namespace,
            "track": track,
        }
        r = self.http.post("/notes", json=body, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def worker_pause(self) -> dict:
        return self.http.post("/worker/pause").raise_for_status().json()

    def worker_resume(self) -> dict:
        return self.http.post("/worker/resume").raise_for_status().json()

    def worker_status(self) -> dict:
        return self.http.get("/worker/status").raise_for_status().json()

    def worker_tick(
        self,
        *,
        min_community_size: int = 3,
        project_ids: list[str] | None = None,
        mode: str = "per_project",
    ) -> dict:
        body: dict = {"min_community_size": min_community_size, "mode": mode}
        if project_ids is not None:
            body["project_ids"] = project_ids
        return self.http.post("/worker/tick", json=body, timeout=120).raise_for_status().json()

    def communities(self, *, project_id: str | None = None, limit: int = 50) -> list[dict]:
        params: dict = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        return self.http.get("/communities", params=params).raise_for_status().json()


def from_env() -> GClient:
    """환경변수 `GSTAR_SERVER_URL` 또는 ctx 활성 `gstar.server_url` 기반 생성.

    `/search/fused` 같은 상위 엔드포인트는 G + Gateway 병합이라 응답 10~15s 걸릴 수 있어
    env `GSTAR_TIMEOUT` 으로 override 가능 (기본 60s).
    """
    import os
    url = os.environ.get("GSTAR_SERVER_URL") or os.environ.get("G_URL")
    if not url:
        try:
            from ctx.config import Paths as CtxPaths
            from ctx.context import current_context_name, get_key_path, load_context
            cpaths = CtxPaths.load()
            if cpaths.current.exists():
                name = current_context_name(cpaths)
                if name:
                    data = load_context(name, cpaths)
                    url = get_key_path(data, "gstar.server_url")
        except Exception:
            pass
    try:
        timeout = float(os.environ.get("GSTAR_TIMEOUT", "60"))
    except ValueError:
        timeout = 60.0
    return GClient(base_url=url or "http://100.79.251.53:9999", timeout=timeout)
