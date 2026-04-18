"""DuckStore 에서 자동 채워지는 hash chain + namespace 독립성."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.integrity.verify import verify_chain
from gstar.schema import Node
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    s = DuckStore(tmp_path / "g.duckdb")
    try:
        yield s
    finally:
        s.close()


def test_insert_populates_content_hash_and_prev_hash(store):
    a = Node(kind="fact", text="첫번째", source_namespace="test")
    b = Node(kind="fact", text="두번째", source_namespace="test")
    store.insert_node(a)
    store.insert_node(b)

    a_fetched = store.get_node(a.id)
    b_fetched = store.get_node(b.id)

    assert a_fetched.content_hash != ""
    assert a_fetched.prev_hash is None  # 첫 노드
    assert b_fetched.content_hash != ""
    assert b_fetched.prev_hash == a_fetched.content_hash  # 체인 링크


def test_verify_chain_passes_on_clean_insert(store):
    for i in range(5):
        store.insert_node(Node(kind="fact", text=f"문장 {i}", source_namespace="test"))

    nodes = store.nodes_by_namespace("test")
    result = verify_chain(nodes)
    assert result.ok
    assert result.checked == 5


def test_verify_chain_detects_tampering(store):
    for i in range(3):
        store.insert_node(Node(kind="fact", text=f"원본 {i}", source_namespace="test"))

    # 직접 SQL 로 가운데 노드의 text 변조 (정상 API로는 불가능한 공격 시뮬)
    nodes = store.nodes_by_namespace("test")
    victim_id = nodes[1].id
    store.conn.execute("UPDATE node SET text = '위조됨' WHERE id = ?", [victim_id])

    nodes_after = store.nodes_by_namespace("test")
    result = verify_chain(nodes_after)
    assert not result.ok
    assert victim_id in result.bad_node_ids


def test_namespace_isolation(store):
    # 두 namespace 는 서로 독립된 체인
    for i in range(3):
        store.insert_node(Node(kind="fact", text=f"A{i}", source_namespace="ns-A"))
    for i in range(3):
        store.insert_node(Node(kind="fact", text=f"B{i}", source_namespace="ns-B"))

    a_nodes = store.nodes_by_namespace("ns-A")
    b_nodes = store.nodes_by_namespace("ns-B")

    # 각각의 첫 노드는 prev_hash=None
    assert a_nodes[0].prev_hash is None
    assert b_nodes[0].prev_hash is None

    # A 를 변조해도 B 체인은 정상
    store.conn.execute(
        "UPDATE node SET text = 'tampered' WHERE id = ?", [a_nodes[1].id]
    )
    a_result = verify_chain(store.nodes_by_namespace("ns-A"))
    b_result = verify_chain(store.nodes_by_namespace("ns-B"))
    assert not a_result.ok
    assert b_result.ok


def test_unsigned_nodes_still_verify(store):
    """미래 호환성: 서명 필드가 비어 있어도 체인은 통과해야 한다."""

    for i in range(3):
        n = Node(kind="fact", text=f"unsigned {i}", source_namespace="test")
        assert n.signer_id is None
        assert n.signature is None
        store.insert_node(n)

    result = verify_chain(store.nodes_by_namespace("test"))
    assert result.ok
