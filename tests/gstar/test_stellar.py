"""Week 5 — stellar 클러스터링 + 창발 감지 + 안정도 단위·통합 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.embedding import DummyEmbedder
from gstar.gravity.field import add_goal
from gstar.integrity.merkle import merkle_root
from gstar.schema import Edge, Node
from gstar.stellar.cluster import build_clusters
from gstar.stellar.emergence import scan_emergence
from gstar.stellar.stability import compute_stability, update_cluster_stability
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


@pytest.fixture
def env(tmp_path: Path):
    store = DuckStore(tmp_path / "g.duckdb")
    embedder = DummyEmbedder(dim=8)
    faiss = FaissStore(tmp_path / "emb.faiss", dim=8)
    yield store, faiss, embedder
    store.close()


def _make_nodes(store, n: int, prefix: str = "n") -> list[Node]:
    out = []
    for i in range(n):
        node = Node(kind="fact", text=f"{prefix}{i}", source_namespace="test")
        store.insert_node(node)
        out.append(node)
    return out


# ---------------- cluster ----------------


def test_cluster_builds_connected_components(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)

    # 두 개의 독립된 component: {a,b,c}, {d,e}
    a, b, c, d, e = _make_nodes(store, 5, prefix="x")
    store.insert_edge(Edge(src=a.id, dst=b.id, kind="co_occurs"))
    store.insert_edge(Edge(src=b.id, dst=c.id, kind="co_occurs"))
    store.insert_edge(Edge(src=d.id, dst=e.id, kind="co_occurs"))

    gravity = {n.id: 1.0 - 0.1 * i for i, n in enumerate([a, b, c, d, e])}
    res = build_clusters(goal, [n.id for n in (a, b, c, d, e)], gravity, store)

    assert len(res.clusters) == 2
    assert res.singletons == 0
    # 중심 노드는 gravity 최댓값 (a 또는 d)
    clusters = store.clusters_for_goal(goal.id)
    centers = {c.center_node_id for c in clusters}
    assert a.id in centers
    assert d.id in centers


def test_cluster_ignores_singletons_below_min(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)
    nodes = _make_nodes(store, 3)
    # 엣지 전혀 없음 → 각 노드가 싱글턴
    gravity = {n.id: 0.5 for n in nodes}
    res = build_clusters(goal, [n.id for n in nodes], gravity, store, min_members=2)
    assert res.clusters == []
    assert res.singletons == 3


def test_cluster_records_merkle_root(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)
    a, b, c = _make_nodes(store, 3)
    store.insert_edge(Edge(src=a.id, dst=b.id, kind="x"))
    store.insert_edge(Edge(src=b.id, dst=c.id, kind="x"))
    gravity = {a.id: 0.9, b.id: 0.8, c.id: 0.7}
    res = build_clusters(goal, [a.id, b.id, c.id], gravity, store)

    clusters = store.clusters_for_goal(goal.id)
    assert len(clusters) == 1
    # Merkle 재계산 일치
    expected = merkle_root(
        sorted(store.get_node(nid).content_hash for nid in [a.id, b.id, c.id])
    )
    assert clusters[0].merkle_root == expected


# ---------------- emergence ----------------


def test_emergence_logs_on_fourth_edge(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)

    center = Node(kind="entity", text="중심", source_namespace="test")
    store.insert_node(center)
    others = _make_nodes(store, 4, prefix="o")

    # 3 연결까지는 안정
    for o in others[:3]:
        store.insert_edge(Edge(src=center.id, dst=o.id, kind="rel"))
    res = scan_emergence(goal, [center.id], store)
    assert res.detected == 0
    assert res.under_threshold == 1

    # 4 번째 엣지 추가 → 창발
    store.insert_edge(Edge(src=center.id, dst=others[3].id, kind="rel"))
    res = scan_emergence(goal, [center.id], store)
    assert res.detected == 1

    events = store.emergence_for_goal(goal.id)
    assert len(events) == 1
    assert events[0].trigger_node_id == center.id
    assert len(events[0].connected_node_ids) == 4


def test_emergence_dedupes_same_set(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)
    c = Node(kind="entity", text="C", source_namespace="test")
    store.insert_node(c)
    nbrs = _make_nodes(store, 4)
    for n in nbrs:
        store.insert_edge(Edge(src=c.id, dst=n.id, kind="x"))

    r1 = scan_emergence(goal, [c.id], store)
    r2 = scan_emergence(goal, [c.id], store)
    assert r1.detected == 1
    assert r2.detected == 0
    assert r2.already_logged == 1


def test_emergence_recall_on_20_node_case(env):
    """합성 벤치 — 2개의 서로 다른 중심 노드가 각각 4개 이웃을 가지면 둘 다 감지."""

    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)

    c1 = Node(kind="entity", text="c1", source_namespace="test")
    c2 = Node(kind="entity", text="c2", source_namespace="test")
    store.insert_node(c1)
    store.insert_node(c2)

    nbrs1 = _make_nodes(store, 4, prefix="a")
    nbrs2 = _make_nodes(store, 4, prefix="b")
    for n in nbrs1:
        store.insert_edge(Edge(src=c1.id, dst=n.id, kind="x"))
    for n in nbrs2:
        store.insert_edge(Edge(src=c2.id, dst=n.id, kind="x"))

    # 추가 주변 노드 12개 — 서로 페어 엣지만 (각자 degree=1, 창발 대상 아님)
    extras = _make_nodes(store, 12, prefix="e")
    for i in range(0, len(extras), 2):
        store.insert_edge(Edge(src=extras[i].id, dst=extras[i + 1].id, kind="x"))

    all_ids = [c1.id, c2.id] + [n.id for n in nbrs1 + nbrs2 + extras]
    res = scan_emergence(goal, all_ids, store)

    # 정확히 2개의 창발 트리거만 감지
    triggers = {ev.trigger_node_id for ev in store.emergence_for_goal(goal.id)}
    assert c1.id in triggers
    assert c2.id in triggers
    # 재현율 = 2/2
    assert res.detected == 2


# ---------------- stability ----------------


def test_stability_high_for_dense_cluster(env):
    store, _, _ = env
    a, b, c = _make_nodes(store, 3)
    # 삼각형 완성 — 밀도 1.0
    store.insert_edge(Edge(src=a.id, dst=b.id, kind="x"))
    store.insert_edge(Edge(src=b.id, dst=c.id, kind="x"))
    store.insert_edge(Edge(src=a.id, dst=c.id, kind="x"))

    s = compute_stability([a.id, b.id, c.id], store)
    assert s.a_relational > 0.7
    assert s.total > 0.6


def test_stability_low_for_sparse_cluster(env):
    store, _, _ = env
    a, b, c = _make_nodes(store, 3)
    # 엣지 없음 → 관계 밀도 0
    s = compute_stability([a.id, b.id, c.id], store)
    assert s.a_relational < 0.3


def test_update_cluster_stability_persists(env):
    store, faiss, embedder = env
    goal, _ = add_goal("G", "proposal", store, faiss, embedder)
    a, b, c = _make_nodes(store, 3)
    for src, dst in [(a.id, b.id), (b.id, c.id), (a.id, c.id)]:
        store.insert_edge(Edge(src=src, dst=dst, kind="x"))
    gravity = {a.id: 0.9, b.id: 0.8, c.id: 0.7}
    res = build_clusters(goal, [a.id, b.id, c.id], gravity, store)
    cid = res.clusters[0]

    score = update_cluster_stability(cid, store)
    assert score.total > 0.4

    clusters = store.clusters_for_goal(goal.id)
    assert abs(clusters[0].stability_score - score.total) < 1e-6
