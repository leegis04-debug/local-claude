"""Phase H6 — G entity/edge → Neo4j 파생 뷰 emitter.

원칙: G 가 canonical. Neo4j 는 관계 뷰.
신규 label `GEntity` + property `g_kind` 로 저장 — legacy Paper/Method/Project/CodeRepo
(수작업 그래프, Phase A3 에서 G 로 역 import 됨) 와 분리.

Edge 도 새 관계 타입 `G_REL` + property `rel_type`/`g_kind` 로 통일. Cypher traversal
시 `(n:GEntity)-[r:G_REL]-(m:GEntity)` 패턴.

Gateway `/graph/query` 를 통한 Cypher 실행. ASST_TOKEN 필요 (server 컨테이너 env).
derived_view(view='neo4j') 에 매핑 기록.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Neo4jMirrorResult:
    scanned_nodes: int = 0
    upserted_nodes: int = 0
    scanned_edges: int = 0
    upserted_edges: int = 0
    failed: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")


def _token() -> str:
    return os.environ.get("ASST_TOKEN", "")


def _cypher(stmt: str, params: dict | None = None, timeout: int = 30) -> list[dict]:
    body = json.dumps({"cypher": stmt, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"{_gateway_url().rstrip('/')}/graph/query",
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": _token()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d.get("results") or []


def _already_mirrored_node(store, g_node_id: str) -> bool:
    with store.lock:
        row = store.conn.execute(
            "SELECT 1 FROM derived_view WHERE g_node_id=? AND view='neo4j' "
            "AND status='ok' LIMIT 1",
            [g_node_id],
        ).fetchone()
    return row is not None


def _mark_mirrored(
    store, g_node_id: str, external_id: str, status: str = "ok",
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with store.lock:
        store.conn.execute(
            "INSERT OR REPLACE INTO derived_view "
            "(g_node_id, view, external_id, collection, emitted_at, status, error) "
            "VALUES (?, 'neo4j', ?, 'GEntity', ?, ?, ?)",
            [g_node_id, external_id, now, status, error],
        )


def _fetch_entity_nodes(store, limit: int) -> list[dict]:
    """derived_view 에 없는 entity 노드."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT n.id, n.text, n.source_namespace, n.attrs_json, n.created_at "
            "FROM node n WHERE n.kind='entity' AND NOT EXISTS ("
            "  SELECT 1 FROM derived_view d WHERE d.g_node_id=n.id AND d.view='neo4j' AND d.status='ok'"
            ") ORDER BY n.created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    out = []
    for r in rows:
        try:
            attrs = json.loads(r[3]) if r[3] else {}
        except Exception:
            attrs = {}
        out.append(
            {
                "id": r[0],
                "text": (r[1] or "")[:200],
                "ns": r[2],
                "attrs": attrs,
            }
        )
    return out


def _fetch_edges_for_mirrored(store, limit: int) -> list[dict]:
    """양쪽 끝 모두 neo4j 로 mirror 된 edge 만 대상."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT e.id, e.src, e.dst, e.kind, COALESCE(e.relation_type, e.kind), e.weight "
            "FROM edge e "
            "JOIN derived_view ds ON ds.g_node_id = e.src AND ds.view='neo4j' AND ds.status='ok' "
            "JOIN derived_view dd ON dd.g_node_id = e.dst AND dd.view='neo4j' AND dd.status='ok' "
            "LEFT JOIN derived_view de ON de.g_node_id = e.id AND de.view='neo4j' "
            "WHERE de.g_node_id IS NULL "
            "LIMIT ?",
            [limit],
        ).fetchall()
    return [
        {
            "id": r[0],
            "src": r[1],
            "dst": r[2],
            "kind": r[3],
            "rel_type": r[4] or r[3],
            "weight": float(r[5] or 1.0),
        }
        for r in rows
    ]


def mirror_to_neo4j(store, *, limit_nodes: int = 500, limit_edges: int = 1000) -> Neo4jMirrorResult:
    """G entity + edge 를 Neo4j GEntity label 로 복제.

    node: MERGE (g:GEntity {g_node_id: $gid}) SET g.text=$t, g.g_kind=$k, g.ns=$ns, g.attrs=$attrs
    edge: MATCH (a:GEntity {g_node_id:$src}), (b:GEntity {g_node_id:$dst})
          MERGE (a)-[r:G_REL {g_edge_id:$eid}]->(b)
          SET r.rel_type=$rt, r.weight=$w
    """
    res = Neo4jMirrorResult()
    if not _token():
        res.errors.append("ASST_TOKEN 없음")
        return res

    # ---- entity 노드 (UNWIND batch 로 1 cypher 당 최대 BATCH 건) ----
    import time
    batch_size = int(os.environ.get("NEO4J_MIRROR_BATCH", "100"))
    nodes = _fetch_entity_nodes(store, limit_nodes)
    res.scanned_nodes = len(nodes)
    for i in range(0, len(nodes), batch_size):
        chunk = nodes[i : i + batch_size]
        items = [{"gid": n["id"], "t": n["text"], "ns": n["ns"]} for n in chunk]
        try:
            _cypher(
                "UNWIND $items AS item "
                "MERGE (g:GEntity {g_node_id: item.gid}) "
                "SET g.text = item.t, g.g_kind = 'entity', g.ns = item.ns",
                {"items": items},
            )
            for n in chunk:
                _mark_mirrored(store, n["id"], n["id"])
            res.upserted_nodes += len(chunk)
        except Exception as exc:
            res.failed += len(chunk)
            res.errors.append(f"node batch {i}: {type(exc).__name__}: {str(exc)[:120]}")
        # 429 방지 — batch 사이 50ms
        time.sleep(0.05)

    # ---- edge (UNWIND batch) ----
    edges = _fetch_edges_for_mirrored(store, limit_edges)
    res.scanned_edges = len(edges)
    for i in range(0, len(edges), batch_size):
        chunk = edges[i : i + batch_size]
        items = [
            {
                "src": e["src"],
                "dst": e["dst"],
                "eid": e["id"],
                "rt": e["rel_type"],
                "w": e["weight"],
            }
            for e in chunk
        ]
        try:
            _cypher(
                "UNWIND $items AS item "
                "MATCH (a:GEntity {g_node_id: item.src}), (b:GEntity {g_node_id: item.dst}) "
                "MERGE (a)-[r:G_REL {g_edge_id: item.eid}]->(b) "
                "SET r.rel_type = item.rt, r.weight = item.w",
                {"items": items},
            )
            for e in chunk:
                _mark_mirrored(store, e["id"], e["id"])
            res.upserted_edges += len(chunk)
        except Exception as exc:
            res.failed += len(chunk)
            if len(res.errors) < 5:
                res.errors.append(f"edge batch {i}: {type(exc).__name__}: {str(exc)[:100]}")
        time.sleep(0.05)

    return res
