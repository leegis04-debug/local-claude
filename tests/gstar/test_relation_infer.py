"""관계 추론 테스트 (순수 로직)."""

from __future__ import annotations

from gstar.ingest.relation_infer import infer_relations


def test_evidence_of_edges_created_for_each_fact():
    entity_to_facts = {
        "e1": {"f1", "f2"},
        "e2": {"f1"},
    }
    edges = infer_relations(entity_to_facts)
    ev = [e for e in edges if e.kind == "evidence_of"]
    assert len(ev) == 3  # e1->f1, e1->f2, e2->f1


def test_co_occurs_weight_equals_cooccurrence_count():
    entity_to_facts = {
        "e1": {"f1", "f2", "f3"},
        "e2": {"f1", "f2"},
        "e3": {"f3"},
    }
    edges = infer_relations(entity_to_facts)
    co = [e for e in edges if e.kind == "co_occurs"]

    # e1↔e2 는 f1, f2 에서 공출현 → weight=2
    e12 = next(e for e in co if {e.src, e.dst} == {"e1", "e2"})
    assert e12.weight == 2.0
    assert set(e12.evidence_ids) == {"f1", "f2"}

    # e1↔e3 는 f3 만 → weight=1
    e13 = next(e for e in co if {e.src, e.dst} == {"e1", "e3"})
    assert e13.weight == 1.0

    # e2↔e3 는 공출현 없음
    assert not any({e.src, e.dst} == {"e2", "e3"} for e in co)


def test_single_entity_fact_no_co_occurs():
    edges = infer_relations({"e1": {"f1"}})
    assert not any(e.kind == "co_occurs" for e in edges)
