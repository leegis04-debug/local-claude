"""Typed graph 쿼리 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.entity.graph import (
    connected_facts,
    neighbors,
    shortest_chain,
    trace_chain,
)
from gstar.entity.types import EntityKind, RelationType
from gstar.schema import Edge as SchemaEdge
from gstar.schema import Node
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def graph_store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    hypothesis = Node(kind="entity", text="H1")
    method = Node(kind="entity", text="Transformer")
    experiment = Node(kind="entity", text="Exp-1")
    result = Node(kind="entity", text="mAP 85.3")
    fact1 = Node(kind="fact", text="H1 을 Transformer 로 검증했다")
    fact2 = Node(kind="fact", text="Exp-1 에서 mAP 85.3 의 결과")
    for n in [hypothesis, method, experiment, result, fact1, fact2]:
        s.insert_node(n)

    _edge_counter = {"n": 0}

    def _insert_edge(src, dst, relation_type, weight=1.0, evidence=None):
        _edge_counter["n"] += 1
        s.conn.execute(
            "INSERT INTO edge (id, src, dst, kind, weight, evidence_json, created_at, relation_type) "
            "VALUES (?, ?, ?, ?, ?, '[]', CURRENT_TIMESTAMP, ?)",
            [f"edge-{_edge_counter['n']}", src, dst, relation_type, weight, relation_type],
        )

    _insert_edge(method.id, hypothesis.id, RelationType.TESTS.value, 2.0)
    _insert_edge(experiment.id, method.id, RelationType.USES.value, 2.0)
    _insert_edge(experiment.id, result.id, RelationType.YIELDS.value, 2.0)
    _insert_edge(hypothesis.id, fact1.id, RelationType.EVIDENCE_OF.value, 1.0)
    _insert_edge(method.id, fact1.id, RelationType.EVIDENCE_OF.value, 1.0)
    _insert_edge(experiment.id, fact2.id, RelationType.EVIDENCE_OF.value, 1.0)
    _insert_edge(result.id, fact2.id, RelationType.EVIDENCE_OF.value, 1.0)

    yield {
        "store": s,
        "hypothesis": hypothesis.id,
        "method": method.id,
        "experiment": experiment.id,
        "result": result.id,
        "fact1": fact1.id,
        "fact2": fact2.id,
    }
    s.close()


def test_neighbors_single_hop(graph_store):
    hits = neighbors(graph_store["method"], graph_store["store"], max_hops=1)
    ids = {h.node_id for h in hits}
    assert graph_store["hypothesis"] in ids
    assert graph_store["experiment"] in ids


def test_neighbors_filter_by_relation_type(graph_store):
    hits = neighbors(
        graph_store["method"],
        graph_store["store"],
        relation_types=[RelationType.TESTS],
        max_hops=1,
    )
    assert len(hits) == 1
    assert hits[0].node_id == graph_store["hypothesis"]


def test_neighbors_multi_hop(graph_store):
    hits = neighbors(graph_store["hypothesis"], graph_store["store"], max_hops=3)
    ids = {h.node_id for h in hits}
    assert graph_store["result"] in ids


def test_connected_facts_returns_evidence_of_facts(graph_store):
    facts = connected_facts(graph_store["hypothesis"], graph_store["store"])
    ids = {f.fact_id for f in facts}
    assert graph_store["fact1"] in ids
    assert graph_store["fact2"] not in ids


def test_trace_chain_hypothesis_to_result(graph_store):
    paths = trace_chain(
        graph_store["hypothesis"],
        EntityKind.OTHER,
        graph_store["store"],
        max_depth=4,
    )
    by_target = trace_chain(
        graph_store["hypothesis"],
        "entity",
        graph_store["store"],
        max_depth=4,
    )
    assert len(by_target) >= 1
    best = by_target[0]
    assert len(best.nodes) >= 2


def test_shortest_chain_prefers_minimum_hops(graph_store):
    sc = shortest_chain(
        graph_store["method"],
        "entity",
        graph_store["store"],
        max_depth=4,
    )
    assert sc is not None
    assert len(sc.edges) >= 1


def test_neighbors_no_self_loop(graph_store):
    hits = neighbors(graph_store["method"], graph_store["store"], max_hops=2)
    assert all(h.node_id != graph_store["method"] for h in hits)


def test_typed_relation_inference_produces_typed_edges():
    from gstar.ingest.relation_infer import infer_relations_typed

    edges = infer_relations_typed(
        {"m": {"f1"}, "h": {"f1"}},
        entity_types={"m": "method", "h": "hypothesis"},
        fact_texts={"f1": "Transformer 를 사용하여 H1 가설을 검증했다"},
    )
    typed = [e for e in edges if e.relation_type == "tests"]
    assert typed, [(e.src, e.dst, e.relation_type) for e in edges]


def test_typed_relation_inference_backward_compat():
    """기존 시그니처 `infer_relations` 는 co_occurs + evidence_of 만."""
    from gstar.ingest.relation_infer import infer_relations

    edges = infer_relations({"a": {"f1"}, "b": {"f1"}})
    kinds = {e.kind for e in edges}
    assert kinds == {"evidence_of", "co_occurs"}
