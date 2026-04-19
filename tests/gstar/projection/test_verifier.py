"""Phase E3 Verifier L1 규칙 검증 테스트.

LLM·Gateway 호출 없이 순수 로직만. L2 는 별도 (Gateway 필요).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gstar.projection.verifier import (
    VerificationReport,
    _extract_numbers,
    _rouge_l_approx,
    verify_l1,
)


def test_extract_numbers_basic():
    out = _extract_numbers("매출 12억원 + 25% 성장")
    assert ("12", "억원") in out
    assert ("25", "%") in out


def test_rouge_l_overlap():
    a = "GraphRAG 는 knowledge graph 기반 검색 방법이다"
    b = "graphrag 는 knowledge graph retrieval"
    score = _rouge_l_approx(a, b)
    assert 0.3 <= score <= 1.0


def test_rouge_l_no_overlap():
    a = "완전히 다른 텍스트 apple"
    b = "아무 관련 없는 orange"
    assert _rouge_l_approx(a, b) < 0.2


def test_verify_l1_number_mismatch():
    section = "우리는 매출 12억원을 목표로 한다."
    facts = [
        {"node_id": "n1", "text": "프로젝트 매출은 15억원 예상", "attrs": {"source": "plan.md"}}
    ]
    rep = verify_l1(section_text=section, facts=facts, track="proposal")
    # 섹션의 12 는 fact 에 없으므로 fail 이슈
    kinds = {i.kind for i in rep.issues}
    assert "number_mismatch" in kinds
    assert not rep.pass_   # fail severity 존재 → pass_=False


def test_verify_l1_clean_passes():
    # 동일 숫자 사용 + citation 존재
    section = "매출 15억원 목표 달성."
    facts = [
        {"node_id": "n1", "text": "매출 15억원 예상 (2026)", "attrs": {"source": "plan.md"}}
    ]
    rep = verify_l1(section_text=section, facts=facts, track="proposal")
    fail_issues = [i for i in rep.issues if i.severity == "fail"]
    assert fail_issues == []


def test_verify_l1_missing_citation_warn():
    # citation 누락은 warn 이지 fail 이 아님 → 전체는 pass
    section = "매출 15억원."
    facts = [{"node_id": "n1", "text": "매출 15억원", "attrs": {}}]
    rep = verify_l1(section_text=section, facts=facts, track="proposal")
    kinds = {i.kind for i in rep.issues}
    assert "missing_citation" in kinds


def test_verify_l1_freshness():
    section = "매출 15억원."
    old_date = datetime.now(timezone.utc) - timedelta(days=800)
    facts = [
        {
            "node_id": "n1",
            "text": "매출 15억원",
            "attrs": {"source": "old.md"},
            "created_at": old_date,
        }
    ]
    rep = verify_l1(
        section_text=section, facts=facts, track="proposal", stale_after_days=365
    )
    kinds = {i.kind for i in rep.issues}
    assert "stale" in kinds
