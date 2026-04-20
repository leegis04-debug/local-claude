"""Phase P-Q2.5 — 질문 중요도 랭킹 검증."""

from __future__ import annotations

from gstar.forms.question_tree import QuestionNode
from gstar.forms.ranking import (
    RankedQuestion,
    rank_questions,
    score_question,
)


def _q(title: str, type_: str = "heading", depth: int = 2, seq: int = 0,
       extras: dict | None = None) -> QuestionNode:
    return QuestionNode(
        id=f"q_{seq:04d}_test", seq=seq, path=f"root > {title}",
        title=title, type=type_, depth=depth, extras=extras or {},
    )


def test_heading_scores_higher_than_answer_slot():
    h = score_question(_q("목표 설정", "heading", depth=2), "idea")
    s = score_question(_q("목표 설정", "table_cell_answer_slot", depth=0), "idea")
    assert h.score > s.score


def test_critical_marker_boosts_score():
    plain = score_question(_q("기술 개요"), "spec")
    crit = score_question(_q("※ 기술 개요 필수"), "spec")
    assert crit.score > plain.score + 4  # ※ + '필수' 가산


def test_stage_keywords_boost():
    # idea stage 에서 '목표' 있는 질문이 더 높게
    a = score_question(_q("일반 제목"), "idea")
    b = score_question(_q("비전 목표 핵심"), "idea")
    assert b.score > a.score


def test_depth_shallow_heading_boosts():
    shallow = score_question(_q("A", "heading", depth=2), "idea")
    deep = score_question(_q("A", "heading", depth=6), "idea")
    assert shallow.score > deep.score


def test_depth_1_meta_penalty():
    # 문서 루트 heading 은 감점 (파일명·HWPX 메타 가능성)
    meta = score_question(_q("HWPX 분석...", "heading", depth=1), "idea")
    normal = score_question(_q("실제 섹션", "heading", depth=2), "idea")
    assert meta.score < normal.score


def test_dilution_patterns_reduce_score():
    real = score_question(_q("실제 핵심 질문"), "idea")
    other = score_question(_q("기타"), "idea")
    assert other.score < real.score


def test_rank_questions_descending_with_seq_tiebreak():
    qs = [
        _q("일반 A", seq=0),
        _q("※ 필수 목표", seq=1),                      # 최고점
        _q("일반 B", seq=2),
        _q("비전 핵심", seq=3),                         # 중간
    ]
    ranked = rank_questions(qs, "idea")
    assert ranked[0].seq == 1  # 최고점 먼저
    # 동점(A, B) 은 seq 순 유지
    a_idx = next(i for i, r in enumerate(ranked) if r.seq == 0)
    b_idx = next(i for i, r in enumerate(ranked) if r.seq == 2)
    assert a_idx < b_idx


def test_rank_breakdown_is_informative():
    r = score_question(_q("※ 필수 목표 수립", "heading", depth=2), "idea")
    # breakdown 에 주요 기여 요소가 기록됨
    assert "critical_marker" in r.breakdown
    assert "stage_keywords" in r.breakdown
    assert "length_sweet" in r.breakdown


def test_answer_slot_numeric_hint_boost():
    plain = score_question(
        _q("연도", "table_cell_answer_slot", extras={"raw_cell": ""}), "proposal"
    )
    with_num = score_question(
        _q("연도", "table_cell_answer_slot", extras={"raw_cell": "2026"}),
        "proposal",
    )
    assert with_num.score > plain.score
