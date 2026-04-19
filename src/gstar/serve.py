"""G FastAPI 서버 — 미니 PC 상주용.

맥북 클라이언트가 호출. Gateway `/search/hybrid` 포맷 호환.
실행:
    g serve --port 9999
    또는
    uvicorn gstar.serve:app --host 0.0.0.0 --port 9999 --workers 1

`--workers 1` 강제: DuckDB 는 single-writer 전제. 다중 worker 는 락 충돌.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
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
        self.faiss = FaissStore(self.paths.faiss, dim=EMBED_DIM_DEFAULT)
        self.weights = Weights.load(self.paths.config)
        self._embedder: Any = None  # lazy: SBERT 로드는 무거워 lifespan 에서 프리로드

    @property
    def embedder(self) -> Any:
        """SBertEmbedder 싱글톤. lifespan 에서 preload, 이후 /search·/ingest 재사용."""
        if self._embedder is None:
            from gstar.embedding.sbert import SBertEmbedder
            self._embedder = SBertEmbedder()
        return self._embedder

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
    s = get_state()
    # SBERT 프리로드 — /search, /ingest/web 첫 호출 지연 제거 (3-8초 → <50ms)
    if os.environ.get("GSTAR_PRELOAD_EMBEDDER", "on").lower() not in ("0", "off", "false", "no"):
        try:
            _ = s.embedder
        except Exception:
            pass
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
    try:
        goal_emb = s.embedder.encode([req.query])[0]
    except ImportError as e:
        raise HTTPException(503, f"embedder unavailable: {e}")
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


# -------- /ingest/web (Web→G 자동 캐시 서버측 ingest) --------


class WebIngestItem(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""


class WebIngestRequest(BaseModel):
    query: str
    namespace: str = "web_cache"
    ttl_days: int = 30
    results: list[WebIngestItem]


class WebIngestResponse(BaseModel):
    facts: int
    entities: int
    edges: int
    skipped_dedupe: int


def _web_url_index_path(s: AppState) -> Path:
    """NAS(또는 GSTAR_HOME) 위 전역 URL 인덱스. TTL dedupe 용."""
    return s.paths.home / "web_url_index.json"


def _load_web_url_index(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_web_url_index(path: Path, idx: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


@app.post("/ingest/web", response_model=WebIngestResponse)
def ingest_web(req: WebIngestRequest):
    """맥북 cache_first_web_search 가 원격 경로로 호출하는 bulk 엔드포인트.

    계약: 맥북이 web_fetch 완료된 content 를 보낸다. 서버는 fetch 하지 않음.
    동작: URL 인덱스 TTL dedupe → staging md 작성 → ingest_path → 인덱스 갱신.
    """
    from gstar.ingest.pipeline import ingest_path

    s = get_state()
    index_path = _web_url_index_path(s)
    index = _load_web_url_index(index_path)
    now_ts = int(time.time())
    ttl_sec = max(req.ttl_days, 0) * 86400

    staging_root = Path(os.environ.get("GSTAR_STAGING", "/tmp/gstar-staging"))
    staging = staging_root / f"{now_ts}_{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=True)

    new_urls: list[tuple[str, str]] = []
    skipped = 0
    try:
        for item in req.results:
            url = (item.url or "").strip()
            if not url:
                continue
            uid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            rec = index.get(uid)
            if rec and rec.get("ingested") and ttl_sec > 0:
                age = now_ts - int(rec.get("fetched_at", 0))
                if age < ttl_sec:
                    skipped += 1
                    continue
            body = item.content or item.snippet or ""
            md = (
                f"<!-- source_url: {url} -->\n"
                f"<!-- query: {req.query} -->\n"
                f"<!-- fetched_at: {now_ts} -->\n"
                f"# {item.title}\n\n"
                f"URL: {url}\n\n"
                f"> {item.snippet}\n\n"
                f"{body}\n"
            )
            (staging / f"{uid}.md").write_text(md, encoding="utf-8")
            new_urls.append((url, uid))

        if not new_urls:
            return WebIngestResponse(facts=0, entities=0, edges=0, skipped_dedupe=skipped)

        try:
            report = ingest_path(
                staging,
                store=s.store,
                faiss=s.faiss,
                embedder=s.embedder,
                namespace=req.namespace,
                track="document",
            )
            s.faiss.save()
        except Exception as exc:
            raise HTTPException(500, f"ingest failed: {exc}")

        for url, uid in new_urls:
            index[uid] = {"url": url, "fetched_at": now_ts, "ingested": True, "ns": req.namespace}
        _save_web_url_index(index_path, index)

        return WebIngestResponse(
            facts=report.facts,
            entities=report.entities,
            edges=report.edges,
            skipped_dedupe=skipped,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
