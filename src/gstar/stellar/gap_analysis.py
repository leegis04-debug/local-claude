"""지식 항성의 3-연결 gap 분석.

G 철학: "노드가 3 연결까지 안정, 4번째부터 창발". 따라서 연결선 < 3 인 노드는 안정성
기여 부족 → web enrich 대상.

핵심 지표:
- `under_connected(cluster_id)` — degree < threshold 인 멤버 목록
- `connection_density(cluster_id)` — 실제 내부 엣지 / 이상적 (3N/2)
- `stability_gap(cluster_id)` — 부족 분량 (enrich 요청 크기 결정)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from gstar.storage.duckdb_store import DuckStore


DEFAULT_STABILITY_DEGREE = 3


@dataclass
class NodeGap:
    node_id: str
    text: str
    kind: str
    current_degree: int
    shortfall: int           # stability_degree - current_degree (positive = under)
    neighbor_ids: list[str] = field(default_factory=list)


@dataclass
class ClusterGap:
    cluster_id: str
    cluster_topic: str
    stability_degree: int
    member_count: int
    internal_edges: int
    ideal_internal_edges: float
    density_ratio: float               # internal_edges / ideal
    under_connected: list[NodeGap]
    stable_ratio: float                # nodes with degree >= stability_degree / total

    @property
    def is_stable(self) -> bool:
        return self.stable_ratio >= 0.9 and self.density_ratio >= 0.85

    @property
    def total_shortfall(self) -> int:
        return sum(g.shortfall for g in self.under_connected)


def _internal_degree(node_id: str, member_ids: set[str], store: DuckStore) -> tuple[int, list[str]]:
    """클러스터 내부 degree + 내부 이웃 목록."""
    deg = 0
    neighbors: list[str] = []
    for e in store.edges_of(node_id):
        other = e.dst if e.src == node_id else e.src
        if other in member_ids and other != node_id:
            deg += 1
            neighbors.append(other)
    return deg, neighbors


def under_connected_nodes(
    member_ids: Iterable[str],
    store: DuckStore,
    *,
    stability_degree: int = DEFAULT_STABILITY_DEGREE,
) -> list[NodeGap]:
    """degree < stability_degree 인 멤버를 NodeGap 으로 반환."""
    member_list = list(member_ids)
    id_set = set(member_list)
    out: list[NodeGap] = []
    for nid in member_list:
        deg, neighbors = _internal_degree(nid, id_set, store)
        if deg >= stability_degree:
            continue
        node = store.get_node(nid)
        if node is None:
            continue
        out.append(
            NodeGap(
                node_id=nid,
                text=node.text,
                kind=node.kind,
                current_degree=deg,
                shortfall=stability_degree - deg,
                neighbor_ids=neighbors,
            )
        )
    out.sort(key=lambda g: (-g.shortfall, g.current_degree))
    return out


def cluster_gap(
    cluster_id: str,
    store: DuckStore,
    *,
    stability_degree: int = DEFAULT_STABILITY_DEGREE,
) -> ClusterGap | None:
    """Cluster 전체 gap 통계 + under-connected 목록."""
    cluster = store.get_cluster(cluster_id)
    if cluster is None:
        return None
    members = [nid for nid, _ in store.cluster_members(cluster_id)]
    if not members:
        return ClusterGap(
            cluster_id=cluster_id,
            cluster_topic="",
            stability_degree=stability_degree,
            member_count=0,
            internal_edges=0,
            ideal_internal_edges=0.0,
            density_ratio=0.0,
            under_connected=[],
            stable_ratio=0.0,
        )

    id_set = set(members)
    internal_edges = 0
    degrees: list[int] = []
    for nid in members:
        deg, _ = _internal_degree(nid, id_set, store)
        degrees.append(deg)
        internal_edges += deg
    internal_edges //= 2

    ideal = len(members) * stability_degree / 2.0
    density_ratio = (internal_edges / ideal) if ideal > 0 else 0.0
    stable_count = sum(1 for d in degrees if d >= stability_degree)
    stable_ratio = stable_count / len(members)

    under = under_connected_nodes(members, store, stability_degree=stability_degree)

    center = store.get_node(cluster.center_node_id) if cluster.center_node_id else None
    topic = center.text if center else ""

    return ClusterGap(
        cluster_id=cluster_id,
        cluster_topic=topic,
        stability_degree=stability_degree,
        member_count=len(members),
        internal_edges=internal_edges,
        ideal_internal_edges=ideal,
        density_ratio=density_ratio,
        under_connected=under,
        stable_ratio=stable_ratio,
    )


def gap_stats_for_nodes(
    node_ids: Iterable[str],
    store: DuckStore,
    *,
    stability_degree: int = DEFAULT_STABILITY_DEGREE,
) -> ClusterGap:
    """Cluster 없이 임의 노드 집합에 대한 gap 분석 (selection loop 중간용).

    loop 중에는 아직 cluster 가 형성 안 됐을 수 있으므로, 현재 kept 노드들로
    가상 cluster 를 가정.
    """
    members = list(node_ids)
    id_set = set(members)
    if not members:
        return ClusterGap(
            cluster_id="", cluster_topic="", stability_degree=stability_degree,
            member_count=0, internal_edges=0, ideal_internal_edges=0.0,
            density_ratio=0.0, under_connected=[], stable_ratio=0.0,
        )

    internal_edges = 0
    degrees: list[int] = []
    for nid in members:
        deg, _ = _internal_degree(nid, id_set, store)
        degrees.append(deg)
        internal_edges += deg
    internal_edges //= 2
    ideal = len(members) * stability_degree / 2.0
    density_ratio = (internal_edges / ideal) if ideal > 0 else 0.0
    stable_count = sum(1 for d in degrees if d >= stability_degree)
    stable_ratio = stable_count / len(members)
    under = under_connected_nodes(members, store, stability_degree=stability_degree)
    return ClusterGap(
        cluster_id="",
        cluster_topic="",
        stability_degree=stability_degree,
        member_count=len(members),
        internal_edges=internal_edges,
        ideal_internal_edges=ideal,
        density_ratio=density_ratio,
        under_connected=under,
        stable_ratio=stable_ratio,
    )
