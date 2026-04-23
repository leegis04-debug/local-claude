"""중력장 계산 — Goal + 노드 그래프 → gravity 점수 리스트.

절차:
1. goal 임베딩으로 FAISS top-K seed 노드 선택
2. seed 에서 BFS 가능한 후보 집합 확장 (adjacency 는 DuckDB edges_of)
3. 후보 제한 (candidate_k) 후 각 score 항목 계산
4. 가중합 → 내림차순 정렬
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from gstar.config import Weights
from gstar.embedding import Embedder
from gstar.gravity import score as S
from gstar.schema import Goal, Node
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


def _max_candidates() -> int:
    """adjacency 확장 후 gravity loop 가 처리할 최대 후보 수. entity-dense
    query 에서 후보가 수천개로 폭발해 fused_search 가 20s+ 가 되는 문제 완화."""
    try:
        return int(os.environ.get("GSTAR_GRAVITY_MAX_CANDIDATES", "800"))
    except ValueError:
        return 800


@dataclass
class GravityEntry:
    node_id: str
    total: float
    breakdown: dict[str, float]


def _faiss_lookup_vector(faiss: FaissStore, node_id: str) -> np.ndarray | None:
    """FaissStore 내부 id 매핑으로 벡터 반환. 없으면 None."""
    idx = faiss.id_to_idx.get(node_id)
    if idx is None:
        return None
    return np.asarray(faiss.index.reconstruct(idx), dtype=np.float32)


def compute_gravity(
    goal: Goal,
    goal_emb: np.ndarray,
    store: DuckStore,
    faiss: FaissStore,
    weights: Weights,
    *,
    now: datetime | None = None,
) -> list[GravityEntry]:
    """주어진 goal 에 대해 모든 노드의 중력 점수 계산."""

    now = now or datetime.now(timezone.utc)

    # 1) seed 선정 (FAISS top-K)
    seed_hits = faiss.search(goal_emb, k=weights.seed_k)
    seed_ids = {nid for nid, _ in seed_hits}

    # 2) 후보 풀: seed + seed 의 k-hop 이웃 + 임베딩 유사 후보 top-N
    candidate_hits = faiss.search(goal_emb, k=weights.candidate_k)
    candidate_ids: set[str] = {nid for nid, _ in candidate_hits}
    candidate_ids |= seed_ids

    # 인접 리스트 (adjacency) — 후보 집합의 엣지를 한 번의 쿼리로 모아온다
    # (과거엔 후보마다 `edges_of` 를 따로 호출해 N회 DB 왕복 → batch 로 1회).
    edge_map = store.edges_of_many(list(candidate_ids))
    adjacency: dict[str, set[str]] = {}
    expanded: set[str] = set()
    for nid in list(candidate_ids):
        edges = edge_map.get(nid, [])
        nbrs = {(e.dst if e.src == nid else e.src) for e in edges}
        adjacency[nid] = nbrs
        # centrality 계산용으로 이웃도 후보에 포함 (별도 set 에 모아서 상한 적용)
        expanded |= nbrs

    # entity-dense query 에선 expanded 가 수천~수만 → gravity 루프 비용 폭발.
    # seed_ids + candidate_hits (FAISS 유사도 상위) 는 항상 유지, 확장분은 상한까지만.
    max_cand = _max_candidates()
    budget = max(0, max_cand - len(candidate_ids))
    if budget > 0 and expanded:
        # seed 를 우선하면 already in candidate_ids. nbrs 중 기존 후보 아닌 것만 budget 만큼 추가.
        extra = [n for n in expanded if n not in candidate_ids][:budget]
        candidate_ids |= set(extra)

    # 3) 모든 후보의 노드·반복선택률도 batch 로 미리 가져온다.
    node_map = store.get_nodes_many(list(candidate_ids))
    stab_map = store.repeat_selection_rate_many(goal.id, list(candidate_ids))

    entries: list[GravityEntry] = []

    for nid in candidate_ids:
        node = node_map.get(nid)
        if node is None or node.kind not in {"fact", "entity", "event", "state", "evidence"}:
            continue

        node_vec = _faiss_lookup_vector(faiss, nid)
        if node_vec is None:
            # 엔티티인데 FAISS 누락 — 관련성 0.
            rel = 0.0
        else:
            rel = S.relevance(node_vec, goal_emb)

        rec = S.recency(node.created_at, halflife_days=weights.halflife_days, now=now)
        cent = S.centrality(nid, seed_ids, adjacency)
        ver = S.version_validity(node)
        pur = S.purpose_fit(node, goal)
        stab = stab_map.get(nid, 0.0)

        total = (
            weights.w_rel * rel
            + weights.w_rec * rec
            + weights.w_cent * cent
            + weights.w_ver * ver
            + weights.w_pur * pur
            + weights.w_stab * stab
        )
        entries.append(
            GravityEntry(
                node_id=nid,
                total=total,
                breakdown={
                    "rel": rel,
                    "rec": rec,
                    "cent": cent,
                    "ver": ver,
                    "pur": pur,
                    "stab": stab,
                },
            )
        )

    entries.sort(key=lambda e: e.total, reverse=True)
    return entries


def add_goal(
    text: str,
    kind: str,
    store: DuckStore,
    faiss: FaissStore,
    embedder: Embedder,
) -> tuple[Goal, np.ndarray]:
    """목표 생성 + 임베딩을 FAISS 에 색인. id 는 goal.id 로 저장."""
    goal = Goal(text=text, kind=kind)  # type: ignore[arg-type]
    store.insert_goal(goal)
    vec = embedder.encode([text])[0]
    faiss.add(goal.id, vec)
    faiss.save()
    return goal, vec


def get_goal_embedding(faiss: FaissStore, goal_id: str) -> np.ndarray | None:
    return _faiss_lookup_vector(faiss, goal_id)
