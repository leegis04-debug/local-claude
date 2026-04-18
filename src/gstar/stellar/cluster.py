"""지식 항성(Cluster) 생성.

비LLM 1차 뭉침 전략: 주어진 노드 집합에서 **연결 성분(connected components)** 을 찾아
각 성분을 한 cluster 로 본다. 중심 노드 = gravity 점수가 가장 높은 멤버.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from gstar.integrity.merkle import merkle_root
from gstar.schema import Cluster, Goal
from gstar.storage.duckdb_store import DuckStore


@dataclass
class BuildResult:
    clusters: list[str]   # cluster ids
    singletons: int        # 이웃 없는 고립 노드 수


def _connected_components(
    node_ids: set[str],
    adjacency: dict[str, set[str]],
) -> list[set[str]]:
    """주어진 집합 내부에서 BFS 로 연결 성분 추출.

    adjacency 는 전체 그래프를 쓰되, 같은 node_ids 내의 이웃만 따라간다.
    """
    remaining = set(node_ids)
    comps: list[set[str]] = []
    while remaining:
        seed = next(iter(remaining))
        comp: set[str] = set()
        stack = [seed]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            for nb in adjacency.get(n, set()):
                if nb in remaining and nb not in comp:
                    stack.append(nb)
        remaining -= comp
        comps.append(comp)
    return comps


def build_clusters(
    goal: Goal,
    kept_node_ids: Iterable[str],
    gravity_by_id: dict[str, float],
    store: DuckStore,
    *,
    cycle: int = 0,
    min_members: int = 2,
) -> BuildResult:
    """선택된 노드들을 클러스터(지식 항성)로 응집.

    반환된 id 들은 DB 에 기록된 cluster row 들. singletons 는 min_members 미만이라
    cluster 화되지 않은 고립 노드 수.
    """

    node_ids = set(kept_node_ids)
    adjacency: dict[str, set[str]] = {}
    for nid in node_ids:
        nbrs: set[str] = set()
        for e in store.edges_of(nid):
            other = e.dst if e.src == nid else e.src
            if other in node_ids:
                nbrs.add(other)
        adjacency[nid] = nbrs

    comps = _connected_components(node_ids, adjacency)
    cluster_ids: list[str] = []
    singletons = 0
    for comp in comps:
        if len(comp) < min_members:
            singletons += 1
            continue
        # 중심 노드 = gravity 점수 최고
        center = max(comp, key=lambda nid: gravity_by_id.get(nid, 0.0))
        mean_gravity = sum(gravity_by_id.get(nid, 0.0) for nid in comp) / len(comp)

        # Merkle root
        hashes = []
        for nid in sorted(comp):
            n = store.get_node(nid)
            if n and n.content_hash:
                hashes.append(n.content_hash)
        mroot = merkle_root(hashes)

        cluster = Cluster(
            goal_id=goal.id,
            center_node_id=center,
            gravity_mean=mean_gravity,
            cycle=cycle,
            merkle_root=mroot,
        )
        member_gravity = {nid: gravity_by_id.get(nid, 0.0) for nid in comp}
        store.insert_cluster(cluster, member_gravity)
        cluster_ids.append(cluster.id)

    return BuildResult(clusters=cluster_ids, singletons=singletons)
