"""Enrich client · policy · drain 테스트 (네트워크 없음)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.enrich.client import DrainReport, MockEnrichStream, drain_queue
from gstar.enrich.policy import (
    EnrichItem,
    EnrichQuota,
    EnrichRequest,
    generate_queries,
    hash_text,
    should_insert,
)
from gstar.schema import Node
from gstar.stellar.gap_analysis import ClusterGap, NodeGap
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def _gap(topic: str, nodes: list[tuple[str, str, int]]) -> ClusterGap:
    under = [
        NodeGap(node_id=nid, text=txt, kind="entity", current_degree=deg, shortfall=3 - deg)
        for (nid, txt, deg) in nodes
    ]
    return ClusterGap(
        cluster_id="c1", cluster_topic=topic, stability_degree=3,
        member_count=len(under), internal_edges=0, ideal_internal_edges=0.0,
        density_ratio=0.0, under_connected=under, stable_ratio=0.0,
    )


def test_generate_queries_combines_topic():
    gap = _gap("외식업", [("n1", "LOEKAL", 0), ("n2", "KAMIS", 1)])
    qs = generate_queries(gap)
    assert len(qs) == 2
    texts = [q for q, _ in qs]
    assert "LOEKAL" in texts[0]
    assert "외식업" in texts[0]


def test_generate_queries_skips_when_topic_in_text():
    gap = _gap("외식업 원가", [("n1", "외식업 KAMIS", 0)])
    qs = generate_queries(gap)
    assert qs[0][0] == "외식업 KAMIS"


def test_should_insert_rejects_short_and_long():
    assert not should_insert("짧아", set())
    assert not should_insert("x" * 2000, set())
    assert should_insert("이 fact 는 충분히 긴 내용을 담고 있다 라는 설명.", set())


def test_hash_text_deterministic():
    h1 = hash_text("동일한 텍스트")
    h2 = hash_text("동일한 텍스트")
    h3 = hash_text("다른 텍스트")
    assert h1 == h2
    assert h1 != h3


def test_enrich_quota_limits():
    q = EnrichQuota(daily_query_limit=3, per_cluster_limit=2)
    assert q.can_query("c1")
    q.record_query("c1")
    q.record_query("c1")
    assert not q.can_query("c1")
    assert q.can_query("c2")


def test_drain_queue_inserts_fact_and_edge(store):
    """MockEnrichStream 로 EnrichItem 3개 주입 → drain 후 DB 상태 검증."""
    anchor_node = Node(kind="entity", text="LOEKAL")
    store.insert_node(anchor_node)

    items = [
        EnrichItem(
            fact_text=f"LOEKAL 는 외식 브랜드이며 관련 매출이 {i*10}% 성장 기록.",
            fact_kind="fact",
            source_url=f"https://example.com/{i}",
            query="LOEKAL 외식업",
            attach_to_node_ids=[anchor_node.id],
            edge_kind="evidence_of",
            confidence=0.5,
        )
        for i in range(3)
    ]
    stream = MockEnrichStream(items)
    stream.start(EnrichRequest(cluster_topic="외식업"))
    import time as _t
    _t.sleep(0.1)
    stream.stop()

    report = drain_queue(stream.queue, store, embedder=None, hash_checker=None)
    assert report.facts_inserted == 3
    assert report.edges_inserted == 3

    fact_count = store.conn.execute(
        "SELECT COUNT(*) FROM node WHERE kind='fact' AND source_namespace='web'"
    ).fetchone()[0]
    assert fact_count == 3

    edge_count = store.conn.execute(
        "SELECT COUNT(*) FROM edge WHERE src=?", [anchor_node.id]
    ).fetchone()[0]
    assert edge_count == 3


def test_drain_queue_dedup_via_hash_checker(store):
    anchor = Node(kind="entity", text="X")
    store.insert_node(anchor)
    item = EnrichItem(
        fact_text="중복 검증용 fact 텍스트 입니다. 충분히 길이가 깁니다.",
        fact_kind="fact",
        source_url="https://x.com",
        query="x",
        attach_to_node_ids=[anchor.id],
    )
    stream = MockEnrichStream([item, item])
    stream.start(EnrichRequest(cluster_topic="t"))
    import time as _t
    _t.sleep(0.1)
    stream.stop()
    seen: set[str] = set()

    def checker(h: str) -> bool:
        if h in seen:
            return True
        seen.add(h)
        return False

    report = drain_queue(stream.queue, store, embedder=None, hash_checker=checker)
    assert report.facts_inserted == 1
    assert report.dropped_duplicate == 1


def test_drain_queue_drops_invalid_length(store):
    short_item = EnrichItem(
        fact_text="짧",
        fact_kind="fact",
        source_url="",
        query="",
    )
    stream = MockEnrichStream([short_item])
    stream.start(EnrichRequest(cluster_topic=""))
    import time as _t
    _t.sleep(0.1)
    stream.stop()
    report = drain_queue(stream.queue, store, embedder=None)
    assert report.facts_inserted == 0
    assert report.dropped_invalid == 1
