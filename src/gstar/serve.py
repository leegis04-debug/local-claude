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

    # D2 warmup — FAISS mmap I/O + DuckDB block cache / query plan 을 미리
    # 데운다. cold 첫 fused_search 9~20s → ~200ms 목표. SBERT preload 만으론
    # 부족 (FAISS IndexFlatIP brute-force 첫 scan + edges_of_many/get_nodes_many
    # DuckDB 쿼리 plan cache 가 비어있어 3~5초 추가됨).
    if os.environ.get("GSTAR_WARMUP_SEARCH", "on").lower() not in ("0", "off", "false", "no"):
        try:
            from gstar.gravity.field import compute_gravity
            from gstar.schema import Goal as _Goal
            import time as _time
            t0 = _time.time()
            warm_query = os.environ.get("GSTAR_WARMUP_QUERY", "warmup")
            goal_emb = s.embedder.encode([warm_query])[0]
            tmp_goal = _Goal(text=warm_query, kind="proposal")
            entries = compute_gravity(tmp_goal, goal_emb, s.store, s.faiss, s.weights)
            # list_rejected · get_node hot path 도 warmup
            try:
                _ = s.store.list_rejected(limit=10000)
            except Exception:
                pass
            for e in entries[:10]:
                try:
                    s.store.get_node(e.node_id)
                except Exception:
                    pass
            print(
                f"[warmup] fused_search primed in {_time.time()-t0:.2f}s "
                f"({len(entries)} gravity entries, faiss_ntotal={s.faiss.index.ntotal})",
                flush=True,
            )
        except Exception as exc:
            print(f"[warmup] skipped: {type(exc).__name__}: {exc}", flush=True)

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


class GraphExpandIn(BaseModel):
    node_ids: list[str]
    hops: int = 1            # 1 or 2 (2 이상은 팽창 위험)
    limit: int = 50          # 최종 neighbor 반환 수 상한
    kinds: list[str] | None = None  # fact/entity/... 필터


class RejectionItem(BaseModel):
    node_id: str
    reason: str = ""
    source_stage: str | None = None
    source_track: str | None = None


class ReinforceNegativeIn(BaseModel):
    rejections: list[RejectionItem]
    rejected_by: str = "claude"


class ReinforceNegativeOut(BaseModel):
    recorded: int
    total_blacklist: int


class GraphExpandNode(BaseModel):
    node_id: str
    kind: str
    text: str
    namespace: str
    distance: int            # seed 로부터 hop 수 (1 또는 2)


class GraphExpandOut(BaseModel):
    nodes: list[GraphExpandNode]
    total_edges_visited: int
    truncated: bool          # limit 로 잘렸는지


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


@app.post("/reinforce/negative", response_model=ReinforceNegativeOut)
def reinforce_negative(body: ReinforceNegativeIn):
    """Claude Layer 2 (jw:refine) 가 거부한 fact 를 blacklist 등록.

    emergence 의 창발 fact 중 Claude 가 근거 없음으로 판단한 node_id 들을
    rejected_facts 테이블에 기록. Selector fused_search/인접 검색에서 이후
    제외될 수 있도록 토대.
    """
    s = get_state()
    recorded = 0
    for r in body.rejections:
        try:
            s.store.mark_rejected(
                r.node_id,
                reason=r.reason,
                rejected_by=body.rejected_by,
                source_stage=r.source_stage,
                source_track=r.source_track,
            )
            recorded += 1
        except Exception:
            continue
    total = len(s.store.list_rejected(limit=100000))
    return ReinforceNegativeOut(recorded=recorded, total_blacklist=total)


@app.get("/reinforce/negative/list", response_model=list[str])
def list_negative(limit: int = Query(200, ge=1, le=10000)):
    s = get_state()
    return s.store.list_rejected(limit=limit)


@app.post("/graph/expand", response_model=GraphExpandOut)
def graph_expand(body: GraphExpandIn):
    """임의 node_ids 집합 → edge 기반 1~2 hop neighbor 확장.

    Stage B(2) — emergence 루프에서 seed fact 의 graph 관계 인접 노드를
    retrieval 후보 풀에 추가해 의미+관계 결합. DuckStore.edges_of_many 로
    batch edge fetch.
    """
    s = get_state()
    hops = max(1, min(2, int(body.hops or 1)))
    seen: set[str] = set(body.node_ids or [])
    all_by_dist: list[tuple[str, int]] = []  # (node_id, distance)
    current = set(body.node_ids or [])
    edges_visited = 0
    for hop in range(1, hops + 1):
        if not current:
            break
        edge_map = s.store.edges_of_many(list(current))
        next_level: set[str] = set()
        for nid, edges in edge_map.items():
            for e in edges:
                edges_visited += 1
                other = e.dst if e.src == nid else e.src
                if other and other not in seen:
                    seen.add(other)
                    next_level.add(other)
        for nid in next_level:
            all_by_dist.append((nid, hop))
        current = next_level

    # limit 적용 + node 데이터 배치 fetch
    truncated = len(all_by_dist) > int(body.limit or 50)
    all_by_dist = all_by_dist[: int(body.limit or 50)]
    node_ids = [nid for nid, _ in all_by_dist]
    node_map = s.store.get_nodes_many(node_ids)
    kind_filter = set(body.kinds) if body.kinds else None

    out_nodes: list[GraphExpandNode] = []
    for nid, dist in all_by_dist:
        n = node_map.get(nid)
        if n is None:
            continue
        if kind_filter and n.kind not in kind_filter:
            continue
        out_nodes.append(
            GraphExpandNode(
                node_id=n.id,
                kind=n.kind,
                text=n.text,
                namespace=n.source_namespace,
                distance=dist,
            )
        )
    return GraphExpandOut(
        nodes=out_nodes,
        total_edges_visited=edges_visited,
        truncated=truncated,
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


# -------- /ingest/nas (Phase A1+A2: NAS 원문 → G + Qdrant meta 주입) --------


class NasIngestRequest(BaseModel):
    path: str                           # 컨테이너 내부 경로 (예: /nas/workspace/articles)
    namespace: str                      # 이관 ns (예: "articles", "books", ...)
    min_entity_count: int = 2
    track: str = "document"
    autosave: bool = True               # 배치 ingest 중엔 False 로 호출 → 마지막에 /faiss/save 1회


class NasIngestResponse(BaseModel):
    namespace: str
    files_scanned: int
    facts: int
    entities: int
    edges: int


@app.post("/ingest/nas", response_model=NasIngestResponse)
def ingest_nas(req: NasIngestRequest):
    """NAS 원문 디렉터리를 G 로 ingest. `GSTAR_QDRANT_META_DIR` 에 덤프가 있으면
    path 매칭으로 attrs 에 Qdrant 메타 주입. 멱등성은 DuckStore content_hash 에 위임."""
    from gstar.ingest.pipeline import ingest_path
    from gstar.ingest.qdrant_meta import load_from_dir, default_index_path

    s = get_state()
    target = Path(req.path)
    if not target.exists():
        raise HTTPException(404, f"path not found: {target}")
    nas_root = Path(os.environ.get("GSTAR_NAS_ROOT", "/nas/workspace"))
    # 허용 경로: nas_root 하위 또는 staging/test 허용용 /tmp
    try:
        target.resolve().relative_to(nas_root.resolve())
    except Exception:
        if not str(target).startswith(("/tmp/", "/app/state/")):
            raise HTTPException(400, f"path must be under {nas_root}")

    meta_dir = default_index_path()
    meta_index = load_from_dir(meta_dir) if meta_dir.exists() else None
    try:
        report = ingest_path(
            target,
            store=s.store,
            faiss=s.faiss,
            embedder=s.embedder,
            root=nas_root if target.is_dir() else None,
            min_entity_count=req.min_entity_count,
            namespace=req.namespace,
            track=req.track,
            meta_index=meta_index,
        )
        if req.autosave:
            s.faiss.save()
    except Exception as exc:
        raise HTTPException(500, f"ingest failed: {exc}")

    return NasIngestResponse(
        namespace=req.namespace,
        files_scanned=report.files_scanned,
        facts=report.facts,
        entities=report.entities,
        edges=report.edges,
    )


@app.post("/faiss/save")
def faiss_save():
    s = get_state()
    try:
        s.faiss.save()
    except Exception as exc:
        raise HTTPException(500, f"faiss save failed: {exc}")
    return {"ok": True, "ntotal": int(s.faiss.index.ntotal)}


# -------- /mirror/wiki/emit + /worker/wiki_mirror (Sprint B: G → markdown 파생 뷰) --------


class WikiEmitRequest(BaseModel):
    out_dir: str | None = None          # 기본 env GSTAR_WIKI_OUT


class WikiEmitResponse(BaseModel):
    out_dir: str
    entities: int
    topics: int
    sources: int
    citations: int = 0
    digests: int = 0
    index_written: bool
    git_pushed: bool
    errors: list[str]


def _run_wiki_emit(out_dir: str | None) -> WikiEmitResponse:
    from pathlib import Path as _P
    from gstar.mirror.wiki_view import emit_all, _out_dir
    s = get_state()
    out = _P(out_dir) if out_dir else _out_dir()
    try:
        r = emit_all(s.store, out)
    except Exception as exc:
        raise HTTPException(500, f"wiki emit failed: {exc}")
    return WikiEmitResponse(
        out_dir=str(out),
        entities=r.entities,
        topics=r.topics,
        sources=r.sources,
        citations=r.citations,
        digests=r.digests,
        index_written=r.index_written,
        git_pushed=r.git_pushed,
        errors=r.errors,
    )


@app.post("/mirror/wiki/emit", response_model=WikiEmitResponse)
def mirror_wiki_emit(req: WikiEmitRequest):
    return _run_wiki_emit(req.out_dir)


@app.post("/worker/wiki_mirror", response_model=WikiEmitResponse)
def worker_wiki_mirror(req: WikiEmitRequest):
    """Worker tick 과 동일 의미의 개별 트리거. HEAVY_STEPS 에 wiki_mirror 포함.
    env WIKI_MIRROR_ENABLED=on (기본)."""
    return _run_wiki_emit(req.out_dir)


class WikiInboxResponse(BaseModel):
    scanned: int
    ingested: int
    skipped: int
    failed: int
    facts: int
    entities: int
    edges: int
    errors: list[str]


class ReconcileResponse(BaseModel):
    scanned: int
    matched_total: int
    still_null: int
    total: int
    method: str


@app.post("/worker/reconcile_entity_canonical", response_model=ReconcileResponse)
def worker_reconcile_entity_canonical():
    """Sprint C D1 — entity_canonical.node_id=NULL 을 CTAS 방식으로 backfill.

    9f7645e 의 UPDATE 방식은 DuckDB ART 인덱스 stale entry 를 trigger 해
    FatalException 유발. 2026-04-24 에 reconcile_canonical_node_ids() 를
    CTAS (CREATE TABLE AS SELECT) + DROP/RENAME + 인덱스 재빌드 방식으로
    전환해 in-place UPDATE 를 완전히 제거.

    채움 전략 (COALESCE):
      1) 기존 node_id 유지
      2) canonical_name ↔ node.text 매칭
      3) alias.alias_surface ↔ node.text 매칭 (surface form fallback)
    """
    from gstar.entity.linker import reconcile_canonical_node_ids
    s = get_state()
    try:
        r = reconcile_canonical_node_ids(s.store)
    except Exception as exc:
        raise HTTPException(500, f"reconcile failed: {exc}")
    return ReconcileResponse(**r)


@app.post("/worker/wiki_inbox", response_model=WikiInboxResponse)
def worker_wiki_inbox():
    """Sprint C #1 — wiki/_inbox/*.md 감지 → G 재흡수. LIGHT_STEPS 소속,
    tick 마다 자동 실행. 직접 트리거도 가능.
    env WIKI_INBOX_ENABLED=on (기본), GSTAR_WIKI_INBOX_NS=personal_notes."""
    from gstar.mirror.wiki_inbox import ingest_inbox
    s = get_state()
    try:
        r = ingest_inbox(s.store, faiss=s.faiss, embedder=s.embedder)
    except Exception as exc:
        raise HTTPException(500, f"wiki inbox failed: {exc}")
    return WikiInboxResponse(
        scanned=r.scanned,
        ingested=r.ingested,
        skipped=r.skipped,
        failed=r.failed,
        facts=r.facts_added,
        entities=r.entities_added,
        edges=r.edges_added,
        errors=r.errors,
    )


# -------- Sprint C #2 — /wiki/* MCP-스타일 원격 read/write --------


class WikiListItem(BaseModel):
    path: str           # wiki root 기준 상대 경로
    size: int
    mtime: str


class WikiListResponse(BaseModel):
    root: str
    count: int
    items: list[WikiListItem]


class WikiReadResponse(BaseModel):
    path: str
    size: int
    content: str


class WikiInboxWriteRequest(BaseModel):
    filename: str       # "my_note.md" 형태. slash 포함 불가
    content: str


class WikiInboxWriteResponse(BaseModel):
    path: str
    size: int


def _wiki_root() -> "Path":
    from pathlib import Path as _P
    from gstar.mirror.wiki_view import _out_dir
    return _out_dir()


def _wiki_resolve(rel_path: str) -> "Path":
    """wiki root 기준 상대 경로를 안전하게 resolve. path traversal 방지."""
    from pathlib import Path as _P
    root = _wiki_root().resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, f"path escapes wiki root: {rel_path}")
    return target


@app.get("/wiki/list", response_model=WikiListResponse)
def wiki_list(
    type: str = "entity",       # entity | topic | source | all
    limit: int = 200,
):
    """파생 뷰 md 목록. 규모 크므로 limit 필수."""
    from datetime import datetime as _dt
    root = _wiki_root()
    if not root.exists():
        return WikiListResponse(root=str(root), count=0, items=[])
    if type == "entity":
        subdir = root / "entities"
    elif type == "topic":
        subdir = root / "topics"
    elif type == "source":
        subdir = root / "sources"
    elif type == "all":
        subdir = root
    else:
        raise HTTPException(400, f"unknown type: {type}")
    files = sorted(subdir.rglob("*.md"))[:max(1, min(int(limit), 2000))]
    items = []
    for p in files:
        try:
            st = p.stat()
            items.append(WikiListItem(
                path=str(p.relative_to(root)),
                size=st.st_size,
                mtime=_dt.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            ))
        except Exception:
            continue
    return WikiListResponse(root=str(root), count=len(items), items=items)


@app.get("/wiki/read", response_model=WikiReadResponse)
def wiki_read(path: str):
    """wiki root 상대 경로 한 개 파일 read. frontmatter 포함 전체 내용."""
    target = _wiki_resolve(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"not found: {path}")
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"read failed: {exc}")
    return WikiReadResponse(path=path, size=target.stat().st_size, content=content)


# -------- Sprint C #C1 — /wiki/search (in-memory title index, emergence 우선조회용) --------


class WikiSearchHit(BaseModel):
    path: str
    title: str
    type: str               # entity | topic | source
    score: float            # 0.0 ~ 1.0
    excerpt: str            # 본문 앞부분 (최대 500자)


class WikiSearchResponse(BaseModel):
    query: str
    count: int
    items: list[WikiSearchHit]


_WIKI_INDEX_CACHE: dict = {"built_at": 0.0, "entries": []}   # entries: [{path,type,title,body}]
_WIKI_INDEX_TTL_S = 600.0


def _wiki_index_build() -> list[dict]:
    """wiki root 스캔해 {path,type,title,body} 리스트 생성. TTL 10분 캐시."""
    import time as _t
    import re as _re
    now = _t.time()
    if _WIKI_INDEX_CACHE["entries"] and now - _WIKI_INDEX_CACHE["built_at"] < _WIKI_INDEX_TTL_S:
        return _WIKI_INDEX_CACHE["entries"]
    root = _wiki_root()
    if not root.exists():
        _WIKI_INDEX_CACHE["entries"] = []
        _WIKI_INDEX_CACHE["built_at"] = now
        return []
    entries: list[dict] = []
    for subtype, subdir in (("entity", "entities"), ("topic", "topics"), ("source", "sources")):
        d = root / subdir
        if not d.exists():
            continue
        for p in d.rglob("*.md"):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            title = ""
            m = _re.search(r'^title:\s*"?([^"\n]+)"?\s*$', text, _re.M)
            if m:
                title = m.group(1).strip()
            if not title:
                m2 = _re.search(r"^#\s+(.+)$", text, _re.M)
                if m2:
                    title = m2.group(1).strip()
            if not title:
                title = p.stem
            body_start = text.find("\n---\n", 3)
            body = text[body_start + 5:] if body_start > 0 else text
            entries.append({
                "path": str(p.relative_to(root)),
                "type": subtype,
                "title": title,
                "body": body[:1500],
            })
    _WIKI_INDEX_CACHE["entries"] = entries
    _WIKI_INDEX_CACHE["built_at"] = now
    return entries


_HANGUL_RE = __import__("re").compile(r"[가-힣]{2,}|[A-Za-z0-9]{2,}")


def _wiki_tokenize(s: str) -> set[str]:
    return {t.lower() for t in _HANGUL_RE.findall(s or "")}


@app.get("/wiki/search", response_model=WikiSearchResponse)
def wiki_search(q: str, top_k: int = 5, types: str = "topic,entity"):
    """wiki 파일 title + body 에 대한 키워드 검색. emergence loop 의 web 호출 전 조회용.

    score = title_overlap*2 + body_overlap (정규화). types 는 콤마 구분 (topic,entity,source).
    """
    if not q or not q.strip():
        return WikiSearchResponse(query=q, count=0, items=[])
    allowed = {t.strip() for t in types.split(",") if t.strip()}
    q_toks = _wiki_tokenize(q)
    if not q_toks:
        return WikiSearchResponse(query=q, count=0, items=[])
    entries = _wiki_index_build()
    scored: list[tuple[float, dict]] = []
    for e in entries:
        if e["type"] not in allowed:
            continue
        t_toks = _wiki_tokenize(e["title"])
        b_toks = _wiki_tokenize(e["body"][:800])
        t_over = len(q_toks & t_toks) / max(len(q_toks), 1)
        b_over = len(q_toks & b_toks) / max(len(q_toks), 1)
        score = t_over * 2.0 + b_over
        if score <= 0:
            continue
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    items = [
        WikiSearchHit(
            path=e["path"],
            title=e["title"],
            type=e["type"],
            score=round(min(s / 3.0, 1.0), 4),
            excerpt=(e["body"][:500] or "").strip(),
        )
        for s, e in scored[: max(1, min(top_k, 20))]
    ]
    return WikiSearchResponse(query=q, count=len(items), items=items)


@app.post("/wiki/inbox/write", response_model=WikiInboxWriteResponse)
def wiki_inbox_write(req: WikiInboxWriteRequest):
    """인간(또는 Claude Code) 이 wiki 에 입력하는 유일한 경로. `_inbox/` 에만
    쓰고, 다음 worker tick(LIGHT_STEPS.wiki_inbox) 이 G 로 재흡수 후 wiki
    regenerate 에 반영. 이름에 slash · 점두어 금지."""
    if "/" in req.filename or req.filename.startswith(".") or not req.filename:
        raise HTTPException(400, "filename must be a plain basename (no slash/dot-prefix)")
    if not req.filename.endswith(".md"):
        raise HTTPException(400, "filename must end with .md")
    inbox = _wiki_root() / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / req.filename
    try:
        target.write_text(req.content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"write failed: {exc}")
    return WikiInboxWriteResponse(
        path=str(target.relative_to(_wiki_root())),
        size=target.stat().st_size,
    )


# -------- /citations/save (Sprint C 2026-04-24 — Gateway 대체) --------


class CitationSaveRequest(BaseModel):
    project: str
    stage: str
    version: str | None = None
    title: str | None = None
    clearance_token: str | None = None
    consistency: int | None = None             # 0~100
    file_path: str | None = None
    line_count: int | None = None
    wip_files: list[str] = []
    decisions: list[str] = []
    tags: list[str] = []
    content: str                               # 산출물 전문
    source: str = "skill"


class CitationSaveResponse(BaseModel):
    id: str
    deduped: bool                              # 동일 content_hash 존재해 재사용한 경우
    content_hash: str
    project: str
    stage: str


@app.post("/citations/save", response_model=CitationSaveResponse)
def citations_save(req: CitationSaveRequest):
    """산출물 전문 + 메타데이터 저장. content_hash 로 dedup (동일 내용 재저장 시 기존 id 반환).

    Gateway `/citations/save` 대체. G server 단독으로 작동, 인증 불필요.
    """
    import hashlib
    from datetime import datetime as _dt

    from gstar.entity.linker import _ulid  # 기존 ULID helper 재활용

    if not req.content or not req.content.strip():
        raise HTTPException(400, "content must be non-empty")
    if not req.project.strip() or not req.stage.strip():
        raise HTTPException(400, "project and stage required")

    ch = hashlib.sha256(req.content.encode("utf-8")).hexdigest()
    s = get_state()

    # dedup: 동일 content_hash 있으면 그 id 반환 (재저장 없음)
    with s.store.lock:
        existing = s.store.conn.execute(
            "SELECT id FROM citation_artifact WHERE content_hash = ? LIMIT 1",
            [ch],
        ).fetchone()
    if existing:
        return CitationSaveResponse(
            id=existing[0],
            deduped=True,
            content_hash=ch,
            project=req.project,
            stage=req.stage,
        )

    cid = _ulid()
    with s.store.lock:
        s.store.conn.execute(
            """INSERT INTO citation_artifact (
                id, project, stage, version, title, clearance_token,
                consistency, file_path, line_count,
                wip_files_json, decisions_json, tags_json,
                content, content_hash, source, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                cid, req.project, req.stage, req.version, req.title,
                req.clearance_token, req.consistency, req.file_path,
                req.line_count,
                json.dumps(req.wip_files, ensure_ascii=False),
                json.dumps(req.decisions, ensure_ascii=False),
                json.dumps(req.tags, ensure_ascii=False),
                req.content, ch, req.source,
                _dt.utcnow(),
            ],
        )
    return CitationSaveResponse(
        id=cid,
        deduped=False,
        content_hash=ch,
        project=req.project,
        stage=req.stage,
    )


class CitationListItem(BaseModel):
    id: str
    project: str
    stage: str
    version: str | None
    title: str | None
    consistency: int | None
    line_count: int | None
    content_hash: str
    created_at: str


class CitationListResponse(BaseModel):
    project: str
    count: int
    items: list[CitationListItem]


@app.get("/citations/list", response_model=CitationListResponse)
def citations_list(project: str, stage: str | None = None, limit: int = 50):
    """project(+stage) 로 citation 목록 조회. content 제외 (용량 절감)."""
    s = get_state()
    conn = s.store._read_conn()
    if stage:
        rows = conn.execute(
            """SELECT id, project, stage, version, title, consistency, line_count,
                      content_hash, created_at
               FROM citation_artifact
               WHERE project = ? AND stage = ?
               ORDER BY created_at DESC LIMIT ?""",
            [project, stage, max(1, min(limit, 500))],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, project, stage, version, title, consistency, line_count,
                      content_hash, created_at
               FROM citation_artifact
               WHERE project = ?
               ORDER BY created_at DESC LIMIT ?""",
            [project, max(1, min(limit, 500))],
        ).fetchall()
    items = [
        CitationListItem(
            id=r[0], project=r[1], stage=r[2], version=r[3], title=r[4],
            consistency=r[5], line_count=r[6], content_hash=r[7],
            created_at=r[8].isoformat(timespec="seconds") if r[8] else "",
        )
        for r in rows
    ]
    return CitationListResponse(project=project, count=len(items), items=items)


class CitationGetResponse(BaseModel):
    id: str
    project: str
    stage: str
    version: str | None
    title: str | None
    clearance_token: str | None
    consistency: int | None
    file_path: str | None
    line_count: int | None
    wip_files: list[str]
    decisions: list[str]
    tags: list[str]
    content: str
    content_hash: str
    source: str
    created_at: str


@app.get("/citations/get", response_model=CitationGetResponse)
def citations_get(id: str):
    """단일 citation 전문 조회."""
    s = get_state()
    row = s.store._read_conn().execute(
        """SELECT id, project, stage, version, title, clearance_token,
                  consistency, file_path, line_count,
                  wip_files_json, decisions_json, tags_json,
                  content, content_hash, source, created_at
           FROM citation_artifact WHERE id = ?""",
        [id],
    ).fetchone()
    if not row:
        raise HTTPException(404, f"not found: {id}")
    return CitationGetResponse(
        id=row[0], project=row[1], stage=row[2], version=row[3], title=row[4],
        clearance_token=row[5], consistency=row[6], file_path=row[7],
        line_count=row[8],
        wip_files=json.loads(row[9] or "[]"),
        decisions=json.loads(row[10] or "[]"),
        tags=json.loads(row[11] or "[]"),
        content=row[12], content_hash=row[13], source=row[14],
        created_at=row[15].isoformat(timespec="seconds") if row[15] else "",
    )


# -------- /ingest/neo4j (Phase A3: Neo4j dump → entity + edge) --------


class Neo4jIngestRequest(BaseModel):
    dump_dir: str                       # 컨테이너 내부 경로 (nodes.jsonl + edges.jsonl 포함)
    namespace: str = "graph_import"


class Neo4jIngestResponse(BaseModel):
    namespace: str
    nodes_created: int
    nodes_reused: int
    entities_new: int
    entities_reused: int
    edges_created: int
    edges_skipped: int


@app.post("/ingest/neo4j", response_model=Neo4jIngestResponse)
def ingest_neo4j(req: Neo4jIngestRequest):
    """Neo4j dump 디렉터리(nodes.jsonl + edges.jsonl) 를 G 에 upsert."""
    from gstar.ingest.neo4j_mapper import apply_dump

    s = get_state()
    d = Path(req.dump_dir)
    if not d.exists():
        raise HTTPException(404, f"dump_dir not found: {d}")
    nodes_path = d / "nodes.jsonl"
    edges_path = d / "edges.jsonl"
    if not nodes_path.exists():
        raise HTTPException(400, f"nodes.jsonl missing in {d}")
    try:
        result = apply_dump(
            nodes_path=nodes_path,
            edges_path=edges_path,
            store=s.store,
            embedder=s.embedder,
            faiss=s.faiss,
            namespace=req.namespace,
        )
    except Exception as exc:
        raise HTTPException(500, f"neo4j import failed: {exc}")
    return Neo4jIngestResponse(
        namespace=result["namespace"],
        nodes_created=result["nodes_created"],
        nodes_reused=result["nodes_reused"],
        entities_new=result["entities_new"],
        entities_reused=result["entities_reused"],
        edges_created=result["edges_created"],
        edges_skipped=result["edges_skipped"],
    )


# -------- /worker/{pause,resume,status,tick} (Phase B2) --------


class WorkerStatus(BaseModel):
    paused: bool
    pause_flag_path: str
    last_tick_id: str | None = None
    last_tick_started_at: str | None = None
    last_tick_finished_at: str | None = None
    last_tick_status: str | None = None
    last_tick_duration_ms: int | None = None


class TickRequest(BaseModel):
    min_community_size: int = 3
    project_ids: list[str] | None = None
    mode: str = "per_project"           # "per_project" | "global"
    # None → LIGHT_STEPS (community+procedures), ["*"] → 전체, ["community", ...] → 명시
    steps: list[str] | None = None


@app.post("/worker/pause")
def worker_pause():
    from gstar.worker.cycle import set_paused

    set_paused(True)
    return {"paused": True}


@app.post("/worker/resume")
def worker_resume():
    from gstar.worker.cycle import set_paused

    set_paused(False)
    return {"paused": False}


@app.get("/worker/status", response_model=WorkerStatus)
def worker_status():
    from gstar.worker.cycle import is_paused, pause_flag_path

    s = get_state()
    last = {
        "id": None,
        "started_at": None,
        "finished_at": None,
        "status": None,
        "duration_ms": None,
    }
    with s.store.lock:
        try:
            row = s.store.conn.execute(
                "SELECT id, started_at, finished_at, status, duration_ms "
                "FROM worker_tick ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                last = {
                    "id": row[0],
                    "started_at": row[1].isoformat() if row[1] else None,
                    "finished_at": row[2].isoformat() if row[2] else None,
                    "status": row[3],
                    "duration_ms": row[4],
                }
        except Exception:
            pass
    return WorkerStatus(
        paused=is_paused(),
        pause_flag_path=str(pause_flag_path()),
        last_tick_id=last["id"],
        last_tick_started_at=last["started_at"],
        last_tick_finished_at=last["finished_at"],
        last_tick_status=last["status"],
        last_tick_duration_ms=last["duration_ms"],
    )


@app.post("/worker/tick")
def worker_tick(req: TickRequest):
    """한 tick 을 on-demand 로 실행. 상주 워커 없이도 커뮤니티 재계산 가능.

    기본 (req.steps=None): LIGHT_STEPS 만 실행 — community + procedures.
    heavy step (qdrant/neo4j mirror, legacy bridge, code_repos) 은
    `/worker/<name>` 전용 엔드포인트로 개별 호출 권장.
    req.steps=["*"] 을 주면 이전 버전처럼 env 플래그 하에 전체 실행.
    """
    from gstar.worker.cycle import run_cycle

    s = get_state()
    try:
        rep = run_cycle(
            s.store,
            min_community_size=req.min_community_size,
            project_ids=req.project_ids,
            mode=req.mode,
            embedder=s.embedder,
            faiss=s.faiss,
            steps=req.steps,
        )
    except Exception as exc:
        raise HTTPException(500, f"tick failed: {exc}")
    return rep.to_json()


# -------- /worker/<heavy-step> (Phase H10: tick 분리) --------


class QdrantMirrorRequest(BaseModel):
    limit: int | None = None            # None → env QDRANT_MIRROR_LIMIT


@app.post("/worker/qdrant_mirror")
def worker_qdrant_mirror(req: QdrantMirrorRequest):
    """G → Qdrant (`g_mirror` collection) 파생 뷰만 단독 실행."""
    from gstar.mirror.qdrant_view import mirror_to_qdrant

    s = get_state()
    lim = req.limit if req.limit is not None else int(os.environ.get("QDRANT_MIRROR_LIMIT", "1000"))
    try:
        r = mirror_to_qdrant(s.store, s.embedder, limit=lim)
    except Exception as exc:
        raise HTTPException(500, f"qdrant_mirror failed: {type(exc).__name__}: {exc}")
    return {
        "collection": r.collection,
        "scanned": r.scanned,
        "upserted": r.upserted,
        "failed": r.failed,
        "errors": r.errors[:5],
    }


class Neo4jMirrorRequest(BaseModel):
    limit_nodes: int | None = None
    limit_edges: int | None = None


@app.post("/worker/neo4j_mirror")
def worker_neo4j_mirror(req: Neo4jMirrorRequest):
    """G → Neo4j (GEntity/G_REL) 파생 뷰만 단독 실행."""
    from gstar.mirror.neo4j_view import mirror_to_neo4j

    s = get_state()
    ln = req.limit_nodes if req.limit_nodes is not None else int(os.environ.get("NEO4J_MIRROR_NODES", "200"))
    le = req.limit_edges if req.limit_edges is not None else int(os.environ.get("NEO4J_MIRROR_EDGES", "500"))
    try:
        r = mirror_to_neo4j(s.store, limit_nodes=ln, limit_edges=le)
    except Exception as exc:
        raise HTTPException(500, f"neo4j_mirror failed: {type(exc).__name__}: {exc}")
    return {
        "nodes_scanned": r.scanned_nodes,
        "nodes_upserted": r.upserted_nodes,
        "edges_scanned": r.scanned_edges,
        "edges_upserted": r.upserted_edges,
        "failed": r.failed,
        "errors": r.errors[:5],
    }


class LegacyBridgeRequest(BaseModel):
    limit: int | None = None


@app.post("/worker/legacy_bridge")
def worker_legacy_bridge(req: LegacyBridgeRequest):
    """G → legacy Gateway `/search/hybrid` 연결 레이어 (coarse, 3등급)."""
    from gstar.mirror.legacy_bridge import bridge_g_to_legacy

    s = get_state()
    try:
        r = bridge_g_to_legacy(s.store, limit=req.limit)
    except Exception as exc:
        raise HTTPException(500, f"legacy_bridge failed: {type(exc).__name__}: {exc}")
    return {
        "scanned": r.scanned,
        "high": r.high,
        "medium": r.medium,
        "low": r.low,
        "errors": r.errors,
    }


class LegacyFactRequest(BaseModel):
    per_tick: int | None = None


@app.post("/worker/legacy_fact")
def worker_legacy_fact(req: LegacyFactRequest):
    """legacy qdrant_meta chunk → 문장 분해 → G fact 매칭 (fine-grained)."""
    from gstar.mirror.legacy_fact_bridge import bridge_legacy_to_g_facts

    s = get_state()
    # endpoint 호출 자체가 명시적 opt-in 이므로 env gate 우회
    prev = os.environ.get("LEGACY_FACT_BRIDGE_ENABLED")
    os.environ["LEGACY_FACT_BRIDGE_ENABLED"] = "on"
    try:
        r = bridge_legacy_to_g_facts(s.store, per_tick=req.per_tick)
    except Exception as exc:
        raise HTTPException(500, f"legacy_fact failed: {type(exc).__name__}: {exc}")
    finally:
        if prev is None:
            os.environ.pop("LEGACY_FACT_BRIDGE_ENABLED", None)
        else:
            os.environ["LEGACY_FACT_BRIDGE_ENABLED"] = prev
    return {
        "chunks_scanned": r.chunks_scanned,
        "sentences_extracted": r.sentences_extracted,
        "matched_high": r.sentences_matched_high,
        "matched_medium": r.sentences_matched_medium,
        "cursor_after": r.cursor_after,
    }


class CodeReposRequest(BaseModel):
    per_tick: int | None = None


@app.post("/worker/code_repos")
def worker_code_repos(req: CodeReposRequest):
    """NAS code_repos 점진 이관 — tick 당 per_tick 파일만 처리."""
    from gstar.worker.code_repos_ingest import ingest_code_repos_batch

    s = get_state()
    # endpoint 호출 자체가 명시적 opt-in 이므로 env gate 우회
    prev = os.environ.get("CODE_REPOS_ENABLED")
    os.environ["CODE_REPOS_ENABLED"] = "on"
    try:
        r = ingest_code_repos_batch(s.store, s.faiss, s.embedder, per_tick=req.per_tick)
    except Exception as exc:
        raise HTTPException(500, f"code_repos failed: {type(exc).__name__}: {exc}")
    finally:
        if prev is None:
            os.environ.pop("CODE_REPOS_ENABLED", None)
        else:
            os.environ["CODE_REPOS_ENABLED"] = prev
    return {
        "scanned": r.scanned,
        "ingested": r.ingested,
        "errors": r.errors,
        "cursor_before": r.cursor_before,
        "cursor_after": r.cursor_after,
    }


# -------- /notes (Phase D1: G + Qdrant mirror) --------


class NotesRequest(BaseModel):
    text: str
    source: str = "manual"                      # 자유 문자열 — 기록자·skill 이름 등
    tags: list[str] = []
    namespace: str = "personal_notes"
    track: str = "document"


class NotesResponse(BaseModel):
    node_id: str
    namespace: str
    facts: int
    entities: int
    edges: int
    mirrored_qdrant: bool


@app.post("/notes", response_model=NotesResponse)
def notes_add(req: NotesRequest):
    """자유 텍스트 note → G ingest (fact/entity/edge 분해).

    GP_MIRROR_QDRANT=on 이면 Gateway `/notes` 로도 proxy (Qdrant personal_notes 적재).
    실패해도 G 저장 자체는 성공으로 반환 (mirrored_qdrant=false).
    """
    import tempfile
    import urllib.error
    import urllib.request

    from gstar.ingest.pipeline import ingest_path

    s = get_state()

    # 1) 임시 md 파일로 wrap → ingest_path 재사용 (chunker/entity/edge 경로 공유)
    body = req.text if req.text.strip().startswith("#") else f"# note\n\n{req.text}\n"
    if req.tags:
        body += f"\n<!-- tags: {', '.join(req.tags)} -->\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir="/app/state", delete=False, encoding="utf-8") as tf:
        tf.write(body)
        tmp_path = Path(tf.name)

    try:
        report = ingest_path(
            tmp_path,
            store=s.store,
            faiss=s.faiss,
            embedder=s.embedder,
            namespace=req.namespace,
            track=req.track,
        )
        s.faiss.save()
    except Exception as exc:
        raise HTTPException(500, f"note ingest failed: {exc}")
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    # 2) 마지막 노드 id (fact 첫 개) — 호출자가 참조 가능하도록
    first_node_id = ""
    with s.store.lock:
        try:
            row = s.store.conn.execute(
                "SELECT id FROM node WHERE source_namespace=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                [req.namespace],
            ).fetchone()
            first_node_id = row[0] if row else ""
        except Exception:
            first_node_id = ""

    # 3) Qdrant mirror (옵션)
    mirrored = False
    if os.environ.get("GP_MIRROR_QDRANT", "off").lower() in {"on", "1", "true"}:
        gw_url = os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")
        tok = os.environ.get("ASST_TOKEN", "")
        if tok:
            try:
                body_json = json.dumps(
                    {"text": req.text, "source": f"g:{first_node_id}", "tags": ["g_mirror", *req.tags]},
                    ensure_ascii=False,
                ).encode("utf-8")
                r = urllib.request.Request(
                    f"{gw_url.rstrip('/')}/notes",
                    data=body_json,
                    headers={"Content-Type": "application/json", "X-Auth-Token": tok},
                )
                with urllib.request.urlopen(r, timeout=5) as resp:
                    resp.read()
                    mirrored = True
            except Exception:
                mirrored = False

    return NotesResponse(
        node_id=first_node_id,
        namespace=req.namespace,
        facts=report.facts,
        entities=report.entities,
        edges=report.edges,
        mirrored_qdrant=mirrored,
    )


# -------- /search/fused (Phase D2: G + Gateway 통합 wrapper) --------


class FusedSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    namespace: str | None = None
    use_gateway: bool = True
    use_g: bool = True
    gateway_timeout: float = 3.0


class FusedHit(BaseModel):
    """Fused 검색 결과. `origin` 필드가 provenance 태그 ("g" | "gateway_qdrant" | "gateway_neo4j").

    pydantic 은 `_` prefix 필드를 private 로 배제하므로 `origin` 으로 명명.
    """

    model_config = {"populate_by_name": True}

    text: str
    score: float
    source: str                             # original source_namespace or collection
    node_id: str | None = None
    namespace: str | None = None
    origin: str                             # "g" | "gateway_qdrant" | "gateway_neo4j"


@app.post("/search/fused", response_model=list[FusedHit])
def search_fused(req: FusedSearchRequest):
    """G + Gateway 병렬 검색 후 병합. 사용자는 상위 wrapper 만 호출.

    결과에 `_source` 태그를 붙여 어디서 왔는지 추적 가능. 재랭킹은 simple:
      - G score 는 0~1 정규화된 gravity 총점 (현재)
      - Gateway qdrant/neo4j_vector score 는 자체 similarity. 소스별 가중:
          g: 1.0, gateway_qdrant: 0.95, gateway_neo4j: 0.9 (tie 처리용 미세 가중)
    """
    import concurrent.futures
    import urllib.error
    import urllib.request

    s = get_state()
    hits: list[FusedHit] = []

    def _run_g() -> list[FusedHit]:
        if not req.use_g:
            return []
        try:
            goal_emb = s.embedder.encode([req.query])[0]
            tmp_goal = Goal(text=req.query, kind="proposal")
            entries = compute_gravity(tmp_goal, goal_emb, s.store, s.faiss, s.weights)
        except Exception as exc:
            import traceback
            print(f"[_run_g] gravity FAILED: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            return []
        # [blacklist-filter] Claude Layer 2 에서 거부된 fact 는 제외
        try:
            blacklist: set[str] = set(s.store.list_rejected(limit=10000))
        except Exception as exc:
            print(f"[_run_g] blacklist load FAILED: {type(exc).__name__}: {exc}", flush=True)
            blacklist = set()
        out: list[FusedHit] = []
        # blacklist 로 일부가 스킵될 수 있으므로 fetch buffer 를 *2 → *3 로 확대
        for e in entries[: req.top_k * 3]:
            if e.node_id in blacklist:
                continue
            if len(out) >= req.top_k * 2:
                break
            try:
                n = s.store.get_node(e.node_id)
            except Exception:
                continue
            if n is None:
                continue
            if req.namespace and n.source_namespace != req.namespace:
                continue
            try:
                out.append(
                    FusedHit(
                        text=n.text,
                        score=float(e.total) * 1.0,
                        source=f"{n.source_namespace}/{(n.attrs or {}).get('source','')}",
                        node_id=n.id,
                        namespace=n.source_namespace,
                        origin="g",
                    )
                )
            except Exception as exc:
                print(f"[_run_g] FusedHit build FAILED for node {n.id}: {exc}", flush=True)
                continue
        return out

    def _run_gateway() -> list[FusedHit]:
        if not req.use_gateway:
            return []
        gw_url = os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")
        tok = os.environ.get("ASST_TOKEN", "")
        if not tok:
            return []
        body = json.dumps({"query": req.query, "top_k": req.top_k}).encode("utf-8")
        req_http = urllib.request.Request(
            f"{gw_url.rstrip('/')}/search/hybrid",
            data=body,
            headers={"Content-Type": "application/json", "X-Auth-Token": tok},
        )
        try:
            with urllib.request.urlopen(req_http, timeout=req.gateway_timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            print(f"[_run_gateway] HTTP FAILED: {type(exc).__name__}: {exc}", flush=True)
            return []
        results = d.get("results") or d.get("hits") or []
        out: list[FusedHit] = []
        for r_ in results[: req.top_k * 2]:
            src_raw = (r_.get("source") or "").lower()
            if "neo4j" in src_raw or "text2cypher" in src_raw or "community" in src_raw:
                tag = "gateway_neo4j"
                weight = 0.90
            else:
                tag = "gateway_qdrant"
                weight = 0.95
            score = r_.get("score") or r_.get("sim") or 0.0
            out.append(
                FusedHit(
                    text=(r_.get("text") or r_.get("passage") or r_.get("content") or "")[:1000],
                    score=float(score) * weight,
                    source=str(r_.get("source") or r_.get("collection") or ""),
                    node_id=str(r_.get("node_id") or "") or None,
                    namespace=str(r_.get("namespace") or ""),
                    origin=tag,
                )
            )
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_g = ex.submit(_run_g)
        fut_gw = ex.submit(_run_gateway)
        # G는 55k nodes gravity + 첫 호출 SBERT lazy-load 포함 시 ~10초 소요.
        # Gateway 는 빠르게 응답하는 편 (1~2s) 이므로 분리된 timeout.
        try:
            g_hits = fut_g.result(timeout=max(req.gateway_timeout * 2, 30.0))
        except Exception as exc:
            print(f"[fused] G future timeout/err: {type(exc).__name__}: {exc}", flush=True)
            g_hits = []
        try:
            gw_hits = fut_gw.result(timeout=req.gateway_timeout + 2.0)
        except Exception as exc:
            print(f"[fused] Gateway future timeout/err: {type(exc).__name__}: {exc}", flush=True)
            gw_hits = []

    merged = sorted(g_hits + gw_hits, key=lambda x: x.score, reverse=True)[: req.top_k]
    return merged


@app.get("/communities")
def list_communities(project_id: str | None = None, limit: int = 50):
    """community_canonical 최근 목록."""
    s = get_state()
    with s.store.lock:
        try:
            if project_id:
                rows = s.store.conn.execute(
                    "SELECT id, project_id, algorithm, size, label, created_at "
                    "FROM community_canonical WHERE project_id=? "
                    "ORDER BY created_at DESC, size DESC LIMIT ?",
                    [project_id, limit],
                ).fetchall()
            else:
                rows = s.store.conn.execute(
                    "SELECT id, project_id, algorithm, size, label, created_at "
                    "FROM community_canonical "
                    "ORDER BY created_at DESC, size DESC LIMIT ?",
                    [limit],
                ).fetchall()
        except Exception as exc:
            raise HTTPException(500, f"communities failed: {exc}")
    return [
        {
            "id": r[0],
            "project_id": r[1],
            "algorithm": r[2],
            "size": r[3],
            "label": r[4],
            "created_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


# -------- /trace/* (Phase G — Claude-bracketed trace) --------


class TraceRecord(BaseModel):
    source: str                             # "mcp" | "skill" | "hook"
    phase: str                              # "open" | "step" | "tool_use" | "decision" | "close" | "session"
    task_id: str | None = None              # bracket 묶음 id (open 에서 발급)
    description: str
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    thinking: str | None = None
    extras: dict[str, Any] | None = None


class TraceRecorded(BaseModel):
    node_id: str
    task_id: str | None


@app.post("/trace/record", response_model=TraceRecorded)
def trace_record(req: TraceRecord):
    """Claude 세션 trace 를 G 에 저장. Node(kind='trace') + claude_trace 행 동시 삽입."""
    s = get_state()
    node = Node(
        kind="event",                       # schema 의 Literal 집합 안에 있는 값 (트레이스를 이벤트로 분류)
        text=f"[{req.phase}] {req.description[:240]}",
        attrs={"trace_phase": req.phase, "trace_source": req.source, "task_id": req.task_id or ""},
        source_namespace=os.environ.get("G_TRACE_NS", "claude_traces"),
    )
    try:
        s.store.insert_node(node)
    except Exception as exc:
        raise HTTPException(500, f"node insert failed: {exc}")

    # claude_trace 행
    now = node.created_at
    inputs_j = json.dumps(req.inputs or {}, ensure_ascii=False, default=str)
    outputs_j = json.dumps(req.outputs or {}, ensure_ascii=False, default=str)
    extras_j = json.dumps(req.extras or {}, ensure_ascii=False, default=str)
    trace_meta_j = json.dumps(
        {"phase": req.phase, "source": req.source, "task_id": req.task_id},
        ensure_ascii=False,
    )
    with s.store.lock:
        try:
            s.store.conn.execute(
                "INSERT INTO claude_trace "
                "(id, task_id, bracket_phase, source, description, inputs_json, outputs_json, "
                " thinking_text, extras_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    node.id,
                    req.task_id,
                    req.phase,
                    req.source,
                    req.description,
                    inputs_j,
                    outputs_j,
                    req.thinking,
                    extras_j,
                    now,
                ],
            )
            s.store.conn.execute(
                "UPDATE node SET trace_meta_json=? WHERE id=?",
                [trace_meta_j, node.id],
            )
        except Exception as exc:
            raise HTTPException(500, f"claude_trace insert failed: {exc}")

    # 임베딩 색인 (description 기준) — Selector retrieval 을 위해
    try:
        vec = s.embedder.encode([node.text])[0]
        s.faiss.add(node.id, vec)
        s.faiss.save()
    except Exception:
        pass

    return TraceRecorded(node_id=node.id, task_id=req.task_id)


@app.get("/trace/session/{task_id}")
def trace_session(task_id: str):
    """특정 task_id 의 bracket 내부 모든 trace 시간순 반환."""
    s = get_state()
    with s.store.lock:
        try:
            rows = s.store.conn.execute(
                "SELECT id, task_id, bracket_phase, source, description, "
                "inputs_json, outputs_json, thinking_text, extras_json, created_at "
                "FROM claude_trace WHERE task_id=? ORDER BY created_at",
                [task_id],
            ).fetchall()
        except Exception as exc:
            raise HTTPException(500, f"query failed: {exc}")
    return [
        {
            "id": r[0],
            "task_id": r[1],
            "phase": r[2],
            "source": r[3],
            "description": r[4],
            "inputs": _safe_json_loads(r[5]),
            "outputs": _safe_json_loads(r[6]),
            "thinking": r[7],
            "extras": _safe_json_loads(r[8]),
            "created_at": r[9].isoformat() if r[9] else None,
        }
        for r in rows
    ]


@app.get("/trace/patterns")
def trace_patterns(goal_like: str = "", top_k: int = 5):
    """유사 goal 의 절차(procedure) 패턴 반환 — Selector retrieval 용.

    우선순위:
      1. kind='procedure' 노드 (worker G6 miner 가 생성) 중 text SBERT 유사도 top
      2. fallback: claude_trace 에서 description LIKE goal_like 매칭 (단어 기반)
    """
    import re as _re

    s = get_state()
    if not goal_like.strip():
        return []

    out: list[dict] = []

    # 1) 우선: procedure 노드 SBERT 검색
    try:
        vec = s.embedder.encode([goal_like])[0]
        hits = s.faiss.search(vec, k=top_k * 6)
    except Exception:
        hits = []

    with s.store.lock:
        for nid, score in hits:
            if len(out) >= top_k:
                break
            try:
                row = s.store.conn.execute(
                    "SELECT id, kind, text, attrs_json FROM node WHERE id=? AND kind='procedure'",
                    [nid],
                ).fetchone()
            except Exception:
                continue
            if not row:
                continue
            try:
                attrs = json.loads(row[3]) if row[3] else {}
            except Exception:
                attrs = {}
            out.append(
                {
                    "node_id": row[0],
                    "kind": row[1],
                    "text": row[2],
                    "score": float(score),
                    "phase": attrs.get("phase") or "procedure",
                    "task_id": attrs.get("task_id"),
                }
            )

    # 2) Fallback: claude_trace LIKE 검색 (procedure 아직 없을 때)
    if len(out) < top_k:
        tokens = [t for t in _re.findall(r"\w+", goal_like, flags=_re.UNICODE) if len(t) >= 2]
        needed = top_k - len(out)
        seen_ids = {x["node_id"] for x in out}
        with s.store.lock:
            for tok in tokens[:3]:
                if needed <= 0:
                    break
                try:
                    rows = s.store.conn.execute(
                        "SELECT id, task_id, bracket_phase, description, created_at "
                        "FROM claude_trace WHERE description LIKE ? "
                        "ORDER BY created_at DESC LIMIT ?",
                        [f"%{tok}%", needed * 2],
                    ).fetchall()
                except Exception:
                    continue
                for r in rows:
                    if r[0] in seen_ids:
                        continue
                    out.append(
                        {
                            "node_id": r[0],
                            "kind": "event",
                            "text": r[3],
                            "score": 0.5,          # LIKE 매칭의 기본 점수
                            "phase": r[2],
                            "task_id": r[1],
                        }
                    )
                    seen_ids.add(r[0])
                    needed -= 1
                    if needed <= 0:
                        break

    if out:
        return out[:top_k]

    # 3) 최후 fallback (vector hit 만 있을 때)
    out2: list[dict] = []
    with s.store.lock:
        for nid, score in hits:
            try:
                row = s.store.conn.execute(
                    "SELECT id, kind, text, attrs_json FROM node WHERE id=?", [nid]
                ).fetchone()
            except Exception:
                continue
            if not row:
                continue
            if row[1] not in ("procedure", "event"):
                continue
            try:
                attrs = json.loads(row[3]) if row[3] else {}
            except Exception:
                attrs = {}
            out2.append(
                {
                    "node_id": row[0],
                    "kind": row[1],
                    "text": row[2],
                    "score": float(score),
                    "phase": attrs.get("trace_phase") or attrs.get("phase"),
                    "task_id": attrs.get("task_id"),
                }
            )
            if len(out2) >= top_k:
                break
    return out2


def _safe_json_loads(s: str | None) -> dict | list | None:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None
