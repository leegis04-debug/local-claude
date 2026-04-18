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

    def health(self) -> dict:
        return self.http.get("/health").raise_for_status().json()

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


def from_env() -> GClient:
    """환경변수 `GSTAR_SERVER_URL` 또는 ctx 활성 `gstar.server_url` 기반 생성."""
    import os
    url = os.environ.get("GSTAR_SERVER_URL")
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
    return GClient(base_url=url or "http://100.79.251.53:9999")
