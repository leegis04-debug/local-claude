"""Phase P-Q1 — 양식 md 질문 트리 추출 검증."""

from __future__ import annotations

from gstar.forms.question_tree import (
    QuestionNode,
    QuestionTree,
    is_boilerplate,
    parse_questions_from_md,
)


def test_is_boilerplate_skips_meta_and_signature():
    skip = [
        "HWPX 분석: [양식] 농식품...",
        "문서 메타정보",
        "Section 0",
        "Section 12",
        "표 4: 본인(주관기업)은 위 사항을 정확히 확인하였으며 사실과 다름없음에 동의합니다.",
        "표 5: [별지 제2-1호] 사업신청서(종합)",
        "표 7: [별지 제2-2호] 사업신청서(중소기업 단독 또는 주관기업)",
        "↳",
        "1",
        "---",
        "(서명)",
        "(인)",
    ]
    for t in skip:
        assert is_boilerplate(t), f"예상: boilerplate / 실제: not / title={t!r}"


def test_is_boilerplate_keeps_real_questions():
    keep = [
        "표 8: 1. 기업정보",
        "1. 상용화 대상 개요",
        "2. 상용화 대상 시장",
        "핵심 AI 기술",
        "상용화대상 명칭",
        "신청(지원) 유형",
        "기술수준",
    ]
    for t in keep:
        assert not is_boilerplate(t), f"예상: keep / 실제: boilerplate / title={t!r}"


def test_simple_headings_only():
    md = "# A\n## B\n### C\n텍스트 무시\n"
    t = parse_questions_from_md(md)
    assert len(t) == 3
    assert [n.type for n in t.nodes] == ["heading"] * 3
    assert [n.depth for n in t.nodes] == [1, 2, 3]
    # 부모 체인
    assert t.nodes[0].parent_id is None
    assert t.nodes[1].parent_id == t.nodes[0].id
    assert t.nodes[2].parent_id == t.nodes[1].id


def test_heading_stack_pops_correctly():
    md = "# A\n## B\n### C\n## D\n### E\n"
    t = parse_questions_from_md(md)
    by_title = {n.title: n for n in t.nodes}
    # D 는 A 직계 (## depth=2)
    assert by_title["D"].parent_id == by_title["A"].id
    # E 는 D 아래
    assert by_title["E"].parent_id == by_title["D"].id


def test_simple_table_extraction():
    md = (
        "## 질문들\n"
        "| 확인 | 해당 | 미해당 |\n"
        "|---|---|---|\n"
        "| 1 | 농식품인가? |   |   |\n"
        "| 2 | 중소기업인가? |   |   |\n"
    )
    t = parse_questions_from_md(md)
    heads = t.by_type("table_header")
    assert {n.title for n in heads} >= {"확인", "해당", "미해당"}
    rows = t.by_type("table_row_label")
    assert any(n.title == "1" for n in rows)
    slots = t.by_type("table_cell_answer_slot")
    # 2 rows × 빈 셀 2개씩 + col3 (2칸 + 3칸 제각각) = 최소 4
    assert len(slots) >= 4


def test_checkbox_cells_in_header_pass_through():
    """체크박스 + 텍스트 ('□ 예측') 가 header 에 있을 땐 header 로 그대로 추출.

    _is_answer_slot 는 '□' 만 있거나 괄호·공백 뿐인 cell 만 True 처리.
    """
    md = (
        "## 신청과제\n"
        "| 대분류 | □ 예측·의사결정 | □ 제어·자율화 |\n"
        "|---|---|---|\n"
        "| 선택 |   |   |\n"
    )
    t = parse_questions_from_md(md)
    headers = [n.title for n in t.by_type("table_header")]
    assert "대분류" in headers
    assert "□ 예측·의사결정" in headers  # 텍스트 있어 header 로 수용


def test_empty_cell_only_is_filtered_as_slot():
    md = (
        "## X\n"
        "| 이름 | 답 |\n"
        "|---|---|\n"
        "| 주관사 |   |\n"
    )
    t = parse_questions_from_md(md)
    slots = t.by_type("table_cell_answer_slot")
    # '주관사' 행의 빈 답 셀이 slot 으로 잡혀야 함
    assert any("주관사" in n.title for n in slots)


def test_empty_title_ignored():
    md = "#  \n## 실제\n"
    t = parse_questions_from_md(md)
    assert len(t) == 1
    assert t.nodes[0].title == "실제"


def test_stats_and_leaves():
    md = "# A\n## B\n### C\n"
    t = parse_questions_from_md(md)
    s = t.stats()
    assert s["heading"] == 3
    assert s["total"] == 3
    assert s["leaves"] == 1  # C 만 자식 없음


def test_node_ids_stable_and_unique():
    md = "# A\n## A\n"  # 같은 title 이어도 path 가 달라 id 달라야 함
    t = parse_questions_from_md(md)
    ids = [n.id for n in t.nodes]
    assert len(ids) == len(set(ids))


def test_real_template_smoke(tmp_path):
    """실제 양식 스타일 md 로 전체 흐름 smoke."""
    md = (
        "## 표 6: 신청서\n"
        "| 신청과제 | 대분류 | □ 예측 | □ 제어 |\n"
        "|---|---|---|---|\n"
        "|   | 소분류 | □ 재배 | □ 축산 |\n"
        "| 상용화대상 명칭 |   |\n"
        "| ↳ 간단 설명 |   |\n"
    )
    t = parse_questions_from_md(md)
    titles = {n.title for n in t.nodes}
    assert "상용화대상 명칭" in titles
    assert "↳ 간단 설명" in titles
