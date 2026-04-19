"""Community detection on G entity graph — Phase B1.

networkx Louvain 을 사용해 project_id 단위로 entity 들을 커뮤니티로 묶고
`community_canonical` 에 적재, `entity_canonical.community_id` 에 역링크.

GraphRAG 의 멀티홉 문맥 근사.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ulid import ULID


@dataclass
class CommunityResult:
    project_id: str
    algorithm: str
    communities: int
    members_total: int
    skipped_small: int
    updated_entities: int


def _list_entities_by_project(store) -> dict[str, list[tuple[str, str, str]]]:
    """project_id → [(entity_canonical.id, canonical_name, node_id)].

    node_id is None 인 entity 는 graph edge 와 연결 불가하므로 skip.
    """
    with store.lock:
        rows = store.conn.execute(
            "SELECT id, project_id, canonical_name, node_id FROM entity_canonical "
            "WHERE node_id IS NOT NULL"
        ).fetchall()
    out: dict[str, list[tuple[str, str, str]]] = {}
    for r in rows:
        cid, pid, name, nid = r[0], r[1], r[2], r[3]
        out.setdefault(pid, []).append((cid, name, nid))
    return out


def _entity_subgraph_edges(store, node_ids: set[str]) -> list[tuple[str, str, float]]:
    """주어진 entity node_ids 집합 내에서 edge 로 연결된 쌍을 반환.

    co_occurs / uses / defines / cites / depends_on 등 모두 포함. weight 합산.
    """
    if not node_ids:
        return []
    placeholders = ",".join(["?"] * len(node_ids))
    nids = list(node_ids)
    with store.lock:
        try:
            rows = store.conn.execute(
                f"SELECT src, dst, weight FROM edge WHERE src IN ({placeholders}) "
                f"AND dst IN ({placeholders})",
                nids + nids,
            ).fetchall()
        except Exception:
            return []
    return [(r[0], r[1], float(r[2] or 1.0)) for r in rows]


def _all_entities_as_one_project(store) -> dict[str, list[tuple[str, str, str]]]:
    """project_id 경계를 무시하고 entity 를 단일 가상 project 로 묶음.

    Neo4j 이관처럼 cross-project edge 가 다수일 때, project 단위 Louvain 은
    size-1 community 만 만들어 비효율. global 모드에서는 edge 가 실제로 있는
    그래프를 그대로 돌림.
    """
    with store.lock:
        rows = store.conn.execute(
            "SELECT id, canonical_name, node_id FROM entity_canonical "
            "WHERE node_id IS NOT NULL"
        ).fetchall()
    return {"_global": [(r[0], r[1], r[2]) for r in rows]}


def detect_louvain(
    store,
    *,
    project_id: str | None = None,
    min_community_size: int = 3,
    mode: str = "per_project",
) -> list[CommunityResult]:
    """project_id 별 (또는 전체) entity graph 에 Louvain 적용.

    대용량을 피하기 위해 project_id 필터 권장. min_community_size 미만은 무시 + 집계.
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities  # type: ignore[attr-defined]
    except Exception as exc:
        raise RuntimeError(
            "networkx 필요 — pip install 'networkx>=3.0'. (%s)" % exc
        ) from exc

    if mode == "global":
        by_project = _all_entities_as_one_project(store)
    else:
        by_project = _list_entities_by_project(store)
        if project_id is not None:
            by_project = {project_id: by_project.get(project_id, [])}

    ts_now = datetime.now(timezone.utc)
    results: list[CommunityResult] = []

    for pid, ents in by_project.items():
        if len(ents) < min_community_size:
            continue
        node_ids = {e[2] for e in ents}
        edges = _entity_subgraph_edges(store, node_ids)
        if not edges:
            continue

        g = nx.Graph()
        # 노드 추가
        for cid, name, nid in ents:
            g.add_node(nid, cid=cid, name=name)
        for src, dst, w in edges:
            if src in node_ids and dst in node_ids and src != dst:
                if g.has_edge(src, dst):
                    g[src][dst]["weight"] += w
                else:
                    g.add_edge(src, dst, weight=w)

        if g.number_of_edges() == 0:
            continue

        try:
            comms = louvain_communities(g, weight="weight", seed=42)
        except Exception:
            continue

        communities_stored = 0
        members_total = 0
        skipped_small = 0
        updated_entities = 0

        # node_id → canonical_id / name
        nid_to_cid: dict[str, str] = {}
        nid_to_name: dict[str, str] = {}
        for cid, name, nid in ents:
            nid_to_cid[nid] = cid
            nid_to_name[nid] = name

        with store.lock:
            for comm_set in comms:
                member_cids = [nid_to_cid[n] for n in comm_set if n in nid_to_cid]
                size = len(member_cids)
                if size < min_community_size:
                    skipped_small += 1
                    continue
                # 대표 label: 첫 원소의 canonical_name (더 정교하게 하려면 mentions 최다)
                label = next(
                    (nid_to_name[n] for n in comm_set if n in nid_to_name),
                    None,
                )
                comm_id = str(ULID())
                store.conn.execute(
                    "INSERT INTO community_canonical "
                    "(id, project_id, algorithm, members_json, size, label, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        comm_id,
                        pid,
                        "louvain",
                        json.dumps(member_cids, ensure_ascii=False),
                        size,
                        label,
                        ts_now,
                    ],
                )
                communities_stored += 1
                members_total += size

                # entity_canonical.community_id 업데이트
                if member_cids:
                    placeholders = ",".join(["?"] * len(member_cids))
                    store.conn.execute(
                        f"UPDATE entity_canonical SET community_id = ? "
                        f"WHERE id IN ({placeholders})",
                        [comm_id, *member_cids],
                    )
                    updated_entities += size

        results.append(
            CommunityResult(
                project_id=pid,
                algorithm="louvain",
                communities=communities_stored,
                members_total=members_total,
                skipped_small=skipped_small,
                updated_entities=updated_entities,
            )
        )

    return results
