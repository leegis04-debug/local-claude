"""stellar/gap_analysis.py — 3연결 gap 식별 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.schema import Edge, Node
from gstar.stellar.gap_analysis import (
    DEFAULT_STABILITY_DEGREE,
    cluster_gap,
    gap_stats_for_nodes,
    under_connected_nodes,
)
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def _nodes_and_edges(store: DuckStore, n: int, edges: list[tuple[int, int]]) -> list[str]:
    """n개 엔티티 노드 + edges 생성. edges = [(i,j), ...] 0-based index."""
    ids: list[str] = []
    for i in range(n):
        nd = Node(kind="entity", text=f"E{i}")
        store.insert_node(nd)
        ids.append(nd.id)
    ec = 0
    for (i, j) in edges:
        ec += 1
        store.conn.execute(
            "INSERT INTO edge (id, src, dst, kind, weight, evidence_json, created_at, relation_type) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
            [f"e{ec}", ids[i], ids[j], "co_occurs", 1.0, "[]", "co_occurs"],
        )
    return ids


def test_under_connected_all_isolated(store):
    ids = _nodes_and_edges(store, 4, [])
    gaps = under_connected_nodes(ids, store)
    assert len(gaps) == 4
    assert all(g.current_degree == 0 for g in gaps)
    assert all(g.shortfall == 3 for g in gaps)


def test_under_connected_some_saturated(store):
    ids = _nodes_and_edges(
        store, 5,
        [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3)],  # 0:3, 1:3, 2:2, 3:2, 4:0
    )
    gaps = under_connected_nodes(ids, store)
    texts = {g.text for g in gaps}
    assert "E0" not in texts
    assert "E1" not in texts
    assert texts == {"E2", "E3", "E4"}


def test_gap_stats_density_ratio(store):
    ids = _nodes_and_edges(
        store, 4,
        [(0, 1), (0, 2), (1, 2), (2, 3)],
    )
    stats = gap_stats_for_nodes(ids, store)
    assert stats.member_count == 4
    assert stats.internal_edges == 4
    assert stats.ideal_internal_edges == pytest.approx(6.0)
    assert stats.density_ratio == pytest.approx(4.0 / 6.0)
    assert stats.stable_ratio < 1.0


def test_gap_stats_fully_stable(store):
    ids = _nodes_and_edges(
        store, 4,
        [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)],
    )
    stats = gap_stats_for_nodes(ids, store)
    assert stats.stable_ratio == 1.0
    assert stats.density_ratio >= 1.0


def test_is_stable_property():
    from gstar.stellar.gap_analysis import ClusterGap

    cg = ClusterGap(
        cluster_id="c", cluster_topic="t", stability_degree=3,
        member_count=10, internal_edges=15, ideal_internal_edges=15.0,
        density_ratio=1.0, under_connected=[], stable_ratio=1.0,
    )
    assert cg.is_stable
    cg.stable_ratio = 0.5
    assert not cg.is_stable


def test_gap_stats_empty_input(store):
    stats = gap_stats_for_nodes([], store)
    assert stats.member_count == 0
    assert stats.density_ratio == 0.0
    assert stats.under_connected == []


def test_shortfall_ordering(store):
    """shortfall 큰 순으로 정렬되는지."""
    ids = _nodes_and_edges(
        store, 4,
        [(0, 1), (0, 2), (1, 2)],
    )
    gaps = under_connected_nodes(ids, store)
    assert gaps[0].shortfall >= gaps[-1].shortfall


def test_default_stability_degree_is_3():
    assert DEFAULT_STABILITY_DEGREE == 3
