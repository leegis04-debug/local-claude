"""Coherence gate 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.projection.coherence_gate import check
from gstar.projection.fact_registry import upsert
from gstar.projection.summarizer import Fact
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def test_empty_text_ok_in_loose_mode(store):
    v = check("간단한 텍스트.", store, "p", "proposal", coherence_mode="loose")
    assert v.ok


def test_off_mode_always_ok(store):
    v = check("", store, "p", "proposal", coherence_mode="off")
    assert v.ok


def test_missing_entity_kind_flagged(store):
    v = check(
        "본문",
        store,
        "p",
        "proposal",
        required_entity_kinds=["person"],
        coherence_mode="strict",
    )
    assert any(vv.code == "missing_entity_kind" for vv in v.violations)


def test_citation_missing_for_claim(store):
    v = check(
        "따라서 본 시스템은 우수하다. 즉, 기존 방식보다 좋다.",
        store,
        "p",
        "document",
        coherence_mode="strict",
    )
    assert any(vv.code == "citation_missing" for vv in v.violations)


def test_metric_mismatch_detected(store):
    upsert(
        [Fact(text="mAP 85.0%", kind="metric", source_stage="spec", source_hash="h1", track="proposal")],
        store,
        "p",
        track="proposal",
    )
    v = check(
        "본문에서는 mAP 60.0% 라고 주장한다",
        store,
        "p",
        "proposal",
        coherence_mode="strict",
    )
    assert any(vv.code == "metric_mismatch" for vv in v.violations)


def test_strict_mode_rejects_many_warns(store):
    v = check(
        "주장1. 주장2. 주장3.",  # citation_missing 1회
        store,
        "p",
        "document",
        required_entity_kinds=["person", "org", "project"],
        required_fact_kinds=["metric"],
        coherence_mode="strict",
    )
    assert not v.ok
