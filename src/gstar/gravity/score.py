"""중력 점수 개별 항목.

gravity(node, goal) =
    w_rel  * relevance(node, goal)       # 의미 관련성 (A축)
  + w_rec  * recency(node)               # 최신성 (B축)
  + w_cent * centrality(node, seeds)     # 관계 중심성 (A축)
  + w_ver  * version_validity(node)      # 검증성 (B축)
  + w_pur  * purpose_fit(node, goal)     # 목적 적합성
  + w_stab * repeat_selection(node)      # 반복 선택 안정성

모든 항목은 [0, 1] 로 정규화된다.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Mapping

import numpy as np

from gstar.schema import Goal, Node
from gstar.storage.duckdb_store import DuckStore


# ---------------- relevance ----------------

def relevance(node_emb: np.ndarray, goal_emb: np.ndarray) -> float:
    """cosine 유사도를 [0,1] 로 시프트. 입력은 이미 L2 정규화되어 있다고 가정."""
    if node_emb.shape != goal_emb.shape:
        raise ValueError("embedding dim mismatch")
    cos = float(np.dot(node_emb, goal_emb))
    return max(0.0, min(1.0, (cos + 1.0) / 2.0))


# ---------------- recency ----------------

def recency(created_at: datetime, *, halflife_days: float = 30.0, now: datetime | None = None) -> float:
    """지수 감쇠. halflife 지점에서 0.5."""
    if halflife_days <= 0:
        return 1.0
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return float(2 ** (-days / halflife_days))


# ---------------- centrality ----------------

def centrality(
    node_id: str,
    seed_ids: set[str],
    adjacency: Mapping[str, set[str]],
    *,
    max_hops: int = 3,
) -> float:
    """BFS 최단경로 거리 → 역수. 도달 불가면 0."""
    if node_id in seed_ids:
        return 1.0
    frontier = set(seed_ids)
    visited = set(frontier)
    for hop in range(1, max_hops + 1):
        next_front: set[str] = set()
        for n in frontier:
            for nb in adjacency.get(n, set()):
                if nb in visited:
                    continue
                if nb == node_id:
                    return 1.0 / (hop + 1)
                next_front.add(nb)
        visited |= next_front
        frontier = next_front
        if not frontier:
            break
    return 0.0


# ---------------- version validity ----------------

def version_validity(node: Node, is_latest: bool = True) -> float:
    """현재 노드가 최신 버전이면 1.0, 이전 버전이면 감점."""
    if not is_latest:
        return 0.3
    if node.prev_version_id is None:
        return 0.8  # 단일 버전 (미검증)
    return 1.0      # 이력 있는 최신


# ---------------- purpose fit ----------------

_KIND_BASE = {
    ("proposal", "fact"): 0.7,
    ("proposal", "entity"): 0.9,
    ("proposal", "event"): 0.8,
    ("proposal", "state"): 0.6,
    ("proposal", "evidence"): 1.0,
    ("code", "fact"): 0.6,
    ("code", "entity"): 0.8,
    ("code", "event"): 0.5,
    ("code", "state"): 1.0,
    ("research", "fact"): 0.9,
    ("research", "entity"): 0.8,
    ("research", "evidence"): 1.0,
}

_SECTION_BONUS = {
    "proposal": {"배경", "문제", "목표", "기술", "실증", "KPI", "예산"},
    "code": {"입력", "출력", "상태", "제약", "아키텍처"},
    "research": {"문제", "가설", "방법", "결과", "해석"},
}


def purpose_fit(node: Node, goal: Goal) -> float:
    base = _KIND_BASE.get((goal.kind, node.kind), 0.5)
    section = (node.attrs or {}).get("section", "")
    bonus = 0.0
    if section:
        keywords = _SECTION_BONUS.get(goal.kind, set())
        if any(k in section for k in keywords):
            bonus = 0.15
    return min(1.0, base + bonus)


# ---------------- stability ----------------

def repeat_selection(store: DuckStore, goal_id: str, node_id: str, last_n: int = 5) -> float:
    """기존 선택 로그 기반 반복 선택률. 사이클 초기엔 0."""
    return store.repeat_selection_rate(goal_id, node_id, last_n=last_n)


# ---------------- utility ----------------

def logistic(x: float) -> float:
    """경우에 따라 raw 점수를 [0,1] 로 찌그러뜨릴 때 사용."""
    return 1.0 / (1.0 + math.exp(-x))
