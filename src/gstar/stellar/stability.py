"""지식 항성 안정도 — A/B 축 점수.

A (의미·관계): 멤버 간 엣지 밀도 + 중심성 지배력
B (시간·정합성): created_at 분산, 버전 일관성

항성이 붕괴(너무 많은 4연결) 하지 않고 응집(느슨한 엣지) 도 적당한지 판정.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import stdev

from gstar.storage.duckdb_store import DuckStore


@dataclass
class StabilityScore:
    a_relational: float   # [0,1] — 멤버 간 관계 밀도/중심성
    b_temporal: float     # [0,1] — 시간·버전 정합성
    total: float          # (a+b)/2


def compute_stability(
    member_ids: list[str],
    store: DuckStore,
    *,
    max_spread_days: float = 365.0,
) -> StabilityScore:
    if not member_ids:
        return StabilityScore(a_relational=0.0, b_temporal=0.0, total=0.0)

    # ---------- A: 관계 밀도 ----------
    # 멤버 간 엣지 수 / 가능한 페어 수. 중심 노드 degree 지배력도 보정.
    id_set = set(member_ids)
    internal_edges = 0
    degree: dict[str, int] = {nid: 0 for nid in member_ids}
    for nid in member_ids:
        for e in store.edges_of(nid):
            other = e.dst if e.src == nid else e.src
            if other in id_set and other != nid:
                # 양방향 중복 방지 (정렬 기준)
                if (nid, other) < (other, nid):
                    internal_edges += 1
                    degree[nid] += 1
                    degree[other] = degree.get(other, 0) + 1
    n = len(member_ids)
    max_pairs = n * (n - 1) / 2 if n > 1 else 1
    density = internal_edges / max_pairs if max_pairs else 0.0

    # 중심성 지배력: 최대 degree / 총 degree. 지나치게 중심 집중이면 감점.
    total_deg = sum(degree.values())
    if total_deg > 0:
        dominance = max(degree.values()) / total_deg
        centrality_penalty = max(0.0, dominance - 0.6) * 0.5
    else:
        centrality_penalty = 0.0

    a_score = max(0.0, min(1.0, density - centrality_penalty + 0.3 * (density > 0)))

    # ---------- B: 시간 정합성 ----------
    # 멤버들의 created_at 분산. 좁을수록 1.0 (같은 맥락에서 만들어짐).
    created_times: list[datetime] = []
    for nid in member_ids:
        n = store.get_node(nid)
        if n is None:
            continue
        ts = n.created_at
        created_times.append(ts)
    if len(created_times) < 2:
        b_score = 0.7  # 데이터 부족 — 중립
    else:
        span_days = (max(created_times) - min(created_times)).total_seconds() / 86400.0
        b_score = max(0.0, 1.0 - span_days / max_spread_days)

    total = (a_score + b_score) / 2.0
    return StabilityScore(a_relational=a_score, b_temporal=b_score, total=total)


def update_cluster_stability(cluster_id: str, store: DuckStore) -> StabilityScore:
    """cluster 의 stability_score 컬럼을 갱신."""
    members = [nid for nid, _ in store.cluster_members(cluster_id)]
    score = compute_stability(members, store)
    store.conn.execute(
        "UPDATE cluster SET stability_score = ? WHERE id = ?",
        [score.total, cluster_id],
    )
    return score
