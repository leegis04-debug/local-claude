"""Fact registry 테스트 — 충돌 감지·upsert·query."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.projection.fact_registry import (
    Conflict,
    detect_conflicts,
    query,
    upsert,
)
from gstar.projection.summarizer import Fact
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def _fact(kind, text, stage="idea", entity_ids=None) -> Fact:
    return Fact(
        text=text,
        kind=kind,
        source_stage=stage,
        source_hash=f"h-{stage}",
        entity_ids=entity_ids or [],
        track="proposal",
    )


def test_upsert_inserts_new_facts(store):
    facts = [_fact("metric", "mAP 85%"), _fact("decision", "Transformer 채택")]
    r = upsert(facts, store, "proj1", track="proposal")
    assert r.inserted == 2
    assert r.duplicated == 0


def test_upsert_dedupes_identical(store):
    f = _fact("metric", "mAP 85%")
    upsert([f], store, "proj1", track="proposal")
    r = upsert([f], store, "proj1", track="proposal")
    assert r.inserted == 0
    assert r.duplicated == 1


def test_detect_metric_divergence(store):
    old = _fact("metric", "mAP 85.0%", stage="spec")
    new = _fact("metric", "mAP 72.0%", stage="proposal")
    conflicts = detect_conflicts(new, [old], metric_tolerance_pct=5.0)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "metric_diverge"


def test_detect_no_conflict_within_tolerance(store):
    old = _fact("metric", "mAP 85.0%")
    new = _fact("metric", "mAP 84.0%")
    conflicts = detect_conflicts(new, [old], metric_tolerance_pct=5.0)
    assert conflicts == []


def test_query_filters(store):
    upsert(
        [
            _fact("metric", "mAP 85%", stage="spec"),
            _fact("decision", "채택 X", stage="idea"),
            _fact("metric", "F1 0.9", stage="spec"),
        ],
        store,
        "proj1",
        track="proposal",
    )
    metrics = query(store, "proj1", track="proposal", kind="metric")
    assert len(metrics) == 2
    idea_facts = query(store, "proj1", track="proposal", stage="idea")
    assert len(idea_facts) == 1


def test_query_returns_empty_for_missing_project(store):
    assert query(store, "nonexistent", track="proposal") == []


def test_upsert_captures_conflict_in_report(store):
    upsert([_fact("metric", "mAP 85%")], store, "proj1", track="proposal")
    r = upsert(
        [_fact("metric", "mAP 60%", stage="proposal")],
        store,
        "proj1",
        track="proposal",
    )
    assert r.inserted == 1
    assert any(c.kind == "metric_diverge" for c in r.conflicts)
