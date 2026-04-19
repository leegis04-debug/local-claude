"""Enrich 정책 — gap → 검색 쿼리, 중복·쿼터·안전장치."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from gstar.stellar.gap_analysis import ClusterGap, NodeGap


@dataclass
class EnrichRequest:
    """맥북 → 4090 요청 payload."""

    cluster_topic: str = ""
    under_connected: list[dict] = field(default_factory=list)
    stability_degree: int = 3
    max_facts_per_node: int = 3
    namespace: str = "web"
    project_id: str = ""
    request_id: str = ""


@dataclass
class EnrichItem:
    """4090 → 맥북 스트림 단위. 큐에 적재."""

    fact_text: str
    fact_kind: str                       # "fact" | "entity"
    source_url: str
    query: str
    attach_to_node_ids: list[str] = field(default_factory=list)   # edge 생성 대상
    edge_kind: str = "evidence_of"
    confidence: float = 0.5
    content_hash: str = ""


_DOMAIN_HINTS = (
    "통계청", "KAMIS", "한국농촌경제연구원", "한국농식품산업연구원",
    "농림축산식품부", "외식산업연구원",
)


def generate_queries(gap: ClusterGap, *, max_queries: int = 8) -> list[tuple[str, NodeGap]]:
    """under-connected 노드 → 검색 쿼리 리스트 [(query, gap_node), ...].

    전략:
    1. 각 노드 text + cluster_topic 조합
    2. shortfall 큰 순서 우선
    3. 도메인 신뢰 소스 힌트 병합
    """
    out: list[tuple[str, NodeGap]] = []
    topic = gap.cluster_topic.strip() or ""
    topic_tokens = {t for t in topic.split() if len(t) >= 2}
    for node_gap in gap.under_connected[:max_queries]:
        base = node_gap.text.strip()
        if not base or len(base) < 2:
            continue
        base_tokens = {t for t in base.split() if len(t) >= 2}
        if topic and not (topic_tokens & base_tokens):
            q = f"{base} {topic}"
        else:
            q = base
        out.append((q, node_gap))
    return out


def should_insert(
    fact_text: str,
    existing_hashes: set[str],
    *,
    min_len: int = 20,
    max_len: int = 800,
) -> bool:
    """신규 fact 삽입 전 검증."""
    t = fact_text.strip()
    if not t or len(t) < min_len or len(t) > max_len:
        return False
    return True


def hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:24]


@dataclass
class EnrichQuota:
    """일일 쿼터 제어."""

    daily_query_limit: int = 500
    per_cluster_limit: int = 20
    current_daily: int = 0
    current_cluster: dict[str, int] = field(default_factory=dict)

    def can_query(self, cluster_id: str) -> bool:
        if self.current_daily >= self.daily_query_limit:
            return False
        if self.current_cluster.get(cluster_id, 0) >= self.per_cluster_limit:
            return False
        return True

    def record_query(self, cluster_id: str) -> None:
        self.current_daily += 1
        self.current_cluster[cluster_id] = self.current_cluster.get(cluster_id, 0) + 1
