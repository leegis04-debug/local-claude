"""Summarizer 테스트 — Ollama 없이 fallback 경로."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from gstar.projection.context_loader import StageArtifact
from gstar.projection.stage_roles import PROPOSAL_ROLES
from gstar.projection.summarizer import (
    PrevSummary,
    _fallback_extract_facts,
    _fallback_skeleton,
    summarize_prev,
    summarize_stage,
)
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def _art(stage: str, text: str) -> StageArtifact:
    return StageArtifact(
        stage=stage,
        seq=1,
        path=Path("/tmp/x.md"),
        text=text,
        words=len(text),
        created_at=datetime.now(),
    )


def test_fallback_extracts_metric_facts():
    a = _art("spec", "핵심 지표는 AP@0.5 = 85.3% 입니다. mAP 72 도 달성")
    facts = _fallback_extract_facts(a)
    assert any(f.kind == "metric" for f in facts)


def test_fallback_extracts_decision_facts():
    a = _art("idea", "본 과제는 Transformer 를 채택하기로 결정했다")
    facts = _fallback_extract_facts(a)
    assert any(f.kind == "decision" for f in facts)


def test_fallback_skeleton_returns_head_and_tail():
    a = _art(
        "structure",
        "첫 단락. 중요함.\n\n중간 단락.\n\n마지막 단락은 결론",
    )
    skel = _fallback_skeleton(a)
    assert "첫 단락" in skel or "[structure]" in skel


def test_summarize_stage_uses_cache(store):
    a = _art("idea", "목표는 AP 85% 달성이다")
    role = PROPOSAL_ROLES["idea"]
    r1 = summarize_stage(a, role, "proposal", store=store, use_ollama=False)
    r2 = summarize_stage(a, role, "proposal", store=store, use_ollama=False)
    assert r1.source_hash == r2.source_hash
    assert len(r1.facts) == len(r2.facts)


def test_summarize_prev_combines_stages(store):
    a1 = _art("idea", "아이디어는 mAP 80 목표")
    a2 = _art("structure", "방법론 채택")
    role = PROPOSAL_ROLES["structure"]
    prev = summarize_prev([a1, a2], role, "proposal", store=store, use_ollama=False)
    assert len(prev.citations) == 2
    assert "idea" in prev.raw_refs
    assert "structure" in prev.raw_refs


def test_prev_summary_as_prompt_block_respects_max_chars():
    prev = PrevSummary(
        facts=[],
        skeleton="a" * 5000,
        citations=["h1"],
        raw_refs={},
    )
    block = prev.as_prompt_block(max_chars=200)
    assert len(block) <= 200
