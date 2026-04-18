"""Namespace 레지스트리 + Cluster Merkle 통합."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.integrity.merkle import merkle_root
from gstar.schema import Cluster, Goal, Namespace, Node
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    s = DuckStore(tmp_path / "g.duckdb")
    try:
        yield s
    finally:
        s.close()


def test_default_namespace_exists(store):
    names = {ns.name for ns in store.list_namespaces()}
    assert "personal" in names
    assert store.active_namespace() == "personal"


def test_set_active_namespace(store):
    store.upsert_namespace(Namespace(name="daegyeom", description="현 회사"))
    store.set_active_namespace("daegyeom")
    assert store.active_namespace() == "daegyeom"

    # personal 은 비활성
    active = [ns for ns in store.list_namespaces() if ns.is_active]
    assert len(active) == 1
    assert active[0].name == "daegyeom"


def test_cluster_merkle_roundtrip(store):
    goal = Goal(text="목표", kind="proposal")
    store.insert_goal(goal)

    nodes = [Node(kind="fact", text=f"f{i}", source_namespace="test") for i in range(4)]
    for n in nodes:
        store.insert_node(n)

    # cluster 생성
    member_gravity = {n.id: 1.0 - i * 0.1 for i, n in enumerate(nodes)}
    cluster = Cluster(goal_id=goal.id, center_node_id=nodes[0].id)
    store.insert_cluster(cluster, member_gravity)

    # merkle_root 계산 후 업데이트
    hashes = []
    for node_id, _ in store.cluster_members(cluster.id):
        n = store.get_node(node_id)
        hashes.append(n.content_hash)
    root = merkle_root(hashes)
    store.update_cluster_merkle(cluster.id, root)

    # 저장된 root 가 재계산과 동일
    c = store.get_cluster(cluster.id)
    assert c.merkle_root == root


def test_import_preserves_chain_hashes(store):
    """export → import 왕복 시 기존 content_hash/prev_hash 를 그대로 보존."""

    for i in range(3):
        store.insert_node(Node(kind="fact", text=f"t{i}", source_namespace="A"))

    exported = store.nodes_by_namespace("A")

    # 두 번째 store 로 import
    other = DuckStore(store.db_path.parent / "other.duckdb")
    try:
        for n in exported:
            other.insert_node(n)
        imported = other.nodes_by_namespace("A")
        for a, b in zip(exported, imported):
            assert a.content_hash == b.content_hash
            assert a.prev_hash == b.prev_hash
    finally:
        other.close()
