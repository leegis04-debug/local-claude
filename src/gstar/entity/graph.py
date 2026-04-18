"""Typed entity graph 쿼리 API.

기존 `edge` 테이블 + `relation_type` 컬럼을 활용. P축 coherence_gate 와
fact_registry 가 사용.

- `neighbors()` — k-hop 이웃 조회 (relation_type 필터)
- `connected_facts()` — evidence_of 엣지로 연결된 fact 노드
- `trace_chain()` — 시작 entity 에서 target kind 로의 경로 탐색
  (연구: hypothesis → method → experiment → result)
  (코딩: function → test_case)
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass

from gstar.entity.types import Edge, EntityKind, FactRef, RelationType
from gstar.storage.duckdb_store import DuckStore


@dataclass
class NeighborHit:
    node_id: str
    node_kind: str
    node_text: str
    edge: Edge
    distance: int


@dataclass
class ChainPath:
    nodes: list[str]           # 경로상 node_id 시퀀스 (시작 포함)
    edges: list[Edge]          # 인접 쌍별 edge
    target_kind: str


def _edge_row(row) -> Edge:
    evidence = json.loads(row[5]) if row[5] else []
    rt = row[3] if row[3] else row[2]
    try:
        rel = RelationType(rt)
    except ValueError:
        rel = RelationType.CO_OCCURS
    return Edge(
        src=row[0],
        dst=row[1],
        relation_type=rel,
        weight=float(row[4]),
        evidence_ids=evidence,
    )


def _fetch_edges(
    store: DuckStore,
    node_id: str,
    relation_types: list[str] | None = None,
    *,
    direction: str = "both",
) -> list[Edge]:
    cols = "src, dst, kind, relation_type, weight, evidence_json"
    if direction == "out":
        sql = f"SELECT {cols} FROM edge WHERE (src = ?)"
        params: list = [node_id]
    elif direction == "in":
        sql = f"SELECT {cols} FROM edge WHERE (dst = ?)"
        params = [node_id]
    else:
        sql = f"SELECT {cols} FROM edge WHERE (src = ? OR dst = ?)"
        params = [node_id, node_id]
    if relation_types:
        placeholders = ",".join("?" for _ in relation_types)
        sql += f" AND COALESCE(relation_type, kind) IN ({placeholders})"
        params.extend(relation_types)
    rows = store.conn.execute(sql, params).fetchall()
    return [_edge_row(r) for r in rows]


def _get_node_meta(store: DuckStore, node_id: str) -> tuple[str, str] | None:
    row = store.conn.execute(
        "SELECT kind, text FROM node WHERE id = ?", [node_id]
    ).fetchone()
    return (row[0], row[1]) if row else None


def neighbors(
    entity_id: str,
    store: DuckStore,
    *,
    kind: str | EntityKind | None = None,
    relation_types: list[str | RelationType] | None = None,
    max_hops: int = 1,
) -> list[NeighborHit]:
    """BFS 로 k-hop 이웃 반환. kind 필터 적용 가능."""
    if max_hops < 1:
        return []
    rels = [r.value if isinstance(r, RelationType) else r for r in (relation_types or [])] or None
    target_kind = kind.value if isinstance(kind, EntityKind) else kind

    visited: set[str] = {entity_id}
    out: list[NeighborHit] = []
    queue: deque[tuple[str, int]] = deque([(entity_id, 0)])

    while queue:
        cur_id, dist = queue.popleft()
        if dist >= max_hops:
            continue
        for e in _fetch_edges(store, cur_id, rels):
            other = e.dst if e.src == cur_id else e.src
            if other in visited:
                continue
            visited.add(other)
            meta = _get_node_meta(store, other)
            if meta is None:
                continue
            node_kind, node_text = meta
            if target_kind is None or node_kind == target_kind:
                out.append(
                    NeighborHit(
                        node_id=other,
                        node_kind=node_kind,
                        node_text=node_text,
                        edge=e,
                        distance=dist + 1,
                    )
                )
            queue.append((other, dist + 1))
    out.sort(key=lambda h: (h.distance, -h.edge.weight))
    return out


def connected_facts(entity_id: str, store: DuckStore) -> list[FactRef]:
    """evidence_of 엣지로 연결된 fact 노드들."""
    edges = _fetch_edges(
        store,
        entity_id,
        relation_types=[RelationType.EVIDENCE_OF.value],
    )
    out: list[FactRef] = []
    for e in edges:
        fact_id = e.dst if e.src == entity_id else e.src
        row = store.conn.execute(
            "SELECT text, source_namespace FROM node WHERE id = ? AND kind = 'fact'",
            [fact_id],
        ).fetchone()
        if row:
            out.append(FactRef(fact_id=fact_id, text=row[0], source_namespace=row[1]))
    return out


def trace_chain(
    start_entity_id: str,
    target_kind: str | EntityKind,
    store: DuckStore,
    *,
    max_depth: int = 4,
    relation_types: list[str | RelationType] | None = None,
) -> list[ChainPath]:
    """시작 → target_kind 로의 BFS 경로들 (distance <= max_depth)."""
    tk = target_kind.value if isinstance(target_kind, EntityKind) else target_kind
    rels = [r.value if isinstance(r, RelationType) else r for r in (relation_types or [])] or None

    paths: list[ChainPath] = []
    queue: deque[tuple[str, list[str], list[Edge]]] = deque(
        [(start_entity_id, [start_entity_id], [])]
    )
    visited_nodes: set[str] = {start_entity_id}

    while queue:
        cur_id, path_nodes, path_edges = queue.popleft()
        if len(path_edges) >= max_depth:
            continue
        for e in _fetch_edges(store, cur_id, rels):
            other = e.dst if e.src == cur_id else e.src
            if other in path_nodes:
                continue
            meta = _get_node_meta(store, other)
            if meta is None:
                continue
            node_kind, _ = meta
            new_nodes = path_nodes + [other]
            new_edges = path_edges + [e]
            if node_kind == tk:
                paths.append(
                    ChainPath(nodes=new_nodes, edges=new_edges, target_kind=tk)
                )
            if other not in visited_nodes:
                visited_nodes.add(other)
                queue.append((other, new_nodes, new_edges))
    paths.sort(key=lambda p: (len(p.edges), -sum(e.weight for e in p.edges)))
    return paths


def shortest_chain(
    start_entity_id: str,
    target_kind: str | EntityKind,
    store: DuckStore,
    *,
    max_depth: int = 4,
) -> ChainPath | None:
    paths = trace_chain(start_entity_id, target_kind, store, max_depth=max_depth)
    return paths[0] if paths else None
