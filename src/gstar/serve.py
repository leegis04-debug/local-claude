"""G FastAPI 서버 — 미니 PC 상주용.

맥북 클라이언트가 호출. Gateway `/search/hybrid` 포맷 호환.
실행:
    g serve --port 9999
    또는
    uvicorn gstar.serve:app --host 0.0.0.0 --port 9999
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from gstar.config import EMBED_DIM_DEFAULT, Paths, Weights
from gstar.gravity.field import compute_gravity, get_goal_embedding
from gstar.integrity.verify import verify_chain
from gstar.schema import Edge, Goal, Namespace, Node
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


class AppState:
    paths: Paths
    store: DuckStore
    faiss: FaissStore
    weights: Weights

    def __init__(self) -> None:
        self.paths = Paths.load()
        self.paths.ensure()
        self.store = DuckStore(self.paths.db)
        # dim 은 실제 인덱스 파일이 결정. 없으면 default.
        self.faiss = FaissStore(self.paths.faiss, dim=EMBED_DIM_DEFAULT)
        self.weights = Weights.load(self.paths.config)

    def close(self) -> None:
        self.store.close()
        self.faiss.save()


_state: AppState | None = None


def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_state()
    yield
    if _state is not None:
        _state.close()


app = FastAPI(title="g-serve", version="0.1.0", lifespan=lifespan)


# -------- schemas --------


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    namespace: str | None = None


class SearchHit(BaseModel):
    text: str
    score: float
    source: str
    node_id: str
    content_hash: str
    namespace: str


class NodeIn(BaseModel):
    kind: str
    text: str
    attrs: dict[str, Any] = {}
    source_namespace: str = "personal"


class GoalIn(BaseModel):
    text: str
    kind: str = "proposal"


class ChainVerifyOut(BaseModel):
    namespace: str
    ok: bool
    checked: int
    bad_node_ids: list[str]


# -------- endpoints --------


@app.get("/health")
def health() -> dict[str, Any]:
    s = get_state()
    return {
        "ok": True,
        "version": "0.1.0",
        "db": str(s.paths.db),
        "nodes": s.store.count_nodes(),
        "namespaces": [ns.name for ns in s.store.list_namespaces()],
    }


@app.post("/search/hybrid", response_model=list[SearchHit])
def search_hybrid(req: SearchRequest):
    """Gateway 호환 포맷. 임베딩 없는 query 는 400."""
    s = get_state()
    # query 를 Goal 로 취급해 즉석 임베딩을 얻는다. 서버 쪽에서 임베딩 모델 필요.
    try:
        from gstar.embedding.sbert import SBertEmbedder
    except Exception as e:
        raise HTTPException(503, f"embedder unavailable: {e}")

    embedder = SBertEmbedder()
    try:
        goal_emb = embedder.encode([req.query])[0]
    except Exception as e:
        raise HTTPException(500, f"encode failed: {e}")

    # 임시 goal (저장 안 함)
    tmp_goal = Goal(text=req.query, kind="proposal")
    entries = compute_gravity(tmp_goal, goal_emb, s.store, s.faiss, s.weights)

    hits: list[SearchHit] = []
    for e in entries[: req.top_k]:
        n = s.store.get_node(e.node_id)
        if n is None:
            continue
        if req.namespace and n.source_namespace != req.namespace:
            continue
        hits.append(SearchHit(
            text=n.text,
            score=e.total,
            source=f"{n.source_namespace}/{(n.attrs or {}).get('source','')}",
            node_id=n.id,
            content_hash=n.content_hash,
            namespace=n.source_namespace,
        ))
    return hits


@app.get("/nodes/{node_id}")
def get_node(node_id: str):
    s = get_state()
    n = s.store.get_node(node_id)
    if n is None:
        raise HTTPException(404, "node not found")
    return n.model_dump(mode="json")


@app.post("/nodes")
def insert_node_endpoint(body: NodeIn):
    s = get_state()
    n = Node(
        kind=body.kind, text=body.text, attrs=body.attrs,
        source_namespace=body.source_namespace,
    )
    s.store.insert_node(n)
    # upsert namespace 는 ingest 파이프라인만 자동 처리. 여기선 등록 생략(수동).
    return n.model_dump(mode="json")


@app.get("/nodes")
def list_nodes(
    kind: str | None = Query(None),
    namespace: str | None = Query(None),
    limit: int = Query(100),
):
    s = get_state()
    nodes = s.store.list_nodes(kind=kind, namespace=namespace, limit=limit)
    return [n.model_dump(mode="json") for n in nodes]


@app.post("/goals")
def create_goal(body: GoalIn):
    s = get_state()
    goal = Goal(text=body.text, kind=body.kind)  # type: ignore[arg-type]
    s.store.insert_goal(goal)
    return goal.model_dump(mode="json")


@app.get("/goals")
def list_goals():
    s = get_state()
    return [g.model_dump(mode="json") for g in s.store.list_goals()]


@app.get("/clusters/{cluster_id}")
def get_cluster(cluster_id: str):
    s = get_state()
    c = s.store.get_cluster(cluster_id)
    if c is None:
        raise HTTPException(404, "cluster not found")
    members = s.store.cluster_members(cluster_id)
    return {
        "cluster": c.model_dump(mode="json"),
        "members": [{"node_id": nid, "gravity": g} for nid, g in members],
    }


@app.get("/clusters")
def list_clusters(goal_id: str = Query(...)):
    s = get_state()
    return [c.model_dump(mode="json") for c in s.store.clusters_for_goal(goal_id)]


@app.get("/verify/chain", response_model=ChainVerifyOut)
def verify_chain_endpoint(ns: str = Query(...)):
    s = get_state()
    nodes = s.store.nodes_by_namespace(ns)
    res = verify_chain(nodes)
    return ChainVerifyOut(
        namespace=ns, ok=res.ok, checked=res.checked, bad_node_ids=res.bad_node_ids,
    )


@app.get("/namespaces")
def list_ns():
    s = get_state()
    return [ns.model_dump(mode="json") for ns in s.store.list_namespaces()]


# -------- Phase A: entity endpoints --------


class EntityNeighborsOut(BaseModel):
    entity_id: str
    hits: list[dict[str, Any]]


class EntityChainOut(BaseModel):
    start: str
    target_kind: str
    paths: list[dict[str, Any]]


class EntityStatsOut(BaseModel):
    project_id: str | None
    track: str | None
    total_canonical: int
    by_kind: dict[str, int]
    total_aliases: int


@app.get("/entities/{entity_id}/neighbors", response_model=EntityNeighborsOut)
def entity_neighbors(
    entity_id: str,
    kind: str | None = Query(None),
    relation_type: list[str] | None = Query(None),
    max_hops: int = Query(1),
):
    from gstar.entity.graph import neighbors

    s = get_state()
    hits = neighbors(
        entity_id,
        s.store,
        kind=kind,
        relation_types=relation_type,
        max_hops=max_hops,
    )
    return EntityNeighborsOut(
        entity_id=entity_id,
        hits=[
            {
                "node_id": h.node_id,
                "node_kind": h.node_kind,
                "node_text": h.node_text,
                "distance": h.distance,
                "edge": {
                    "src": h.edge.src,
                    "dst": h.edge.dst,
                    "relation_type": h.edge.relation_type.value,
                    "weight": h.edge.weight,
                },
            }
            for h in hits
        ],
    )


@app.get("/entities/chain", response_model=EntityChainOut)
def entity_trace_chain(
    start: str = Query(...),
    target_kind: str = Query(...),
    max_depth: int = Query(4),
    relation_type: list[str] | None = Query(None),
):
    from gstar.entity.graph import trace_chain

    s = get_state()
    paths = trace_chain(
        start,
        target_kind,
        s.store,
        max_depth=max_depth,
        relation_types=relation_type,
    )
    return EntityChainOut(
        start=start,
        target_kind=target_kind,
        paths=[
            {
                "nodes": p.nodes,
                "edges": [
                    {
                        "src": e.src,
                        "dst": e.dst,
                        "relation_type": e.relation_type.value,
                        "weight": e.weight,
                    }
                    for e in p.edges
                ],
            }
            for p in paths
        ],
    )


@app.get("/entities/stats", response_model=EntityStatsOut)
def entity_stats(
    project_id: str = Query(...),
    track: str | None = Query(None),
):
    from gstar.entity.linker import stats

    s = get_state()
    data = stats(s.store, project_id, track)
    return EntityStatsOut(
        project_id=project_id,
        track=track,
        total_canonical=data["total_canonical"],
        by_kind=data["by_kind"],
        total_aliases=data["total_aliases"],
    )


@app.get("/entities")
def list_entities(
    project_id: str = Query(...),
    track: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(100),
):
    from gstar.entity.linker import list_canonicals
    from gstar.entity.types import EntityKind

    s = get_state()
    k_enum = None
    if kind:
        try:
            k_enum = EntityKind(kind)
        except ValueError:
            raise HTTPException(400, f"unknown entity kind: {kind}")
    entities = list_canonicals(s.store, project_id, track, k_enum)[:limit]
    return [
        {
            "id": e.id,
            "canonical_name": e.canonical_name,
            "kind": e.kind.value,
            "track": e.track.value,
            "project_id": e.project_id,
            "mentions": e.mentions,
            "aliases": e.aliases,
        }
        for e in entities
    ]
