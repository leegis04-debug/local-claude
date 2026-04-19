"""Phase P-Q2 — per-question pipeline 보조 함수 검증.

실제 run_pipeline 는 Ollama 필요하므로 여기선 dispatch 판정·form 탐지·goal 포맷만.
"""

from __future__ import annotations

from pathlib import Path

from gstar.forms.question_tree import QuestionNode
from gstar.projection.cli import (
    _find_form_md,
    _question_goal,
    _resolve_stage_dir,
    _should_answer_question,
)


def _mk_q(type_: str, depth: int = 0, title: str = "q", path: str = "root > q") -> QuestionNode:
    return QuestionNode(
        id="q_0001_abc",
        seq=1,
        path=path,
        title=title,
        type=type_,
        depth=depth,
    )


def test_find_form_md_prefers_template_full(tmp_path: Path):
    base = tmp_path / "00-input" / "BASE"
    base.mkdir(parents=True)
    (base / "other.md").write_text("x")
    (base / "template-full.md").write_text("y")
    found = _find_form_md(tmp_path)
    assert found is not None and found.name == "template-full.md"


def test_find_form_md_fallback_largest(tmp_path: Path):
    base = tmp_path / "00-input" / "BASE"
    base.mkdir(parents=True)
    (base / "a.md").write_text("a")
    (base / "b.md").write_text("b" * 1000)
    found = _find_form_md(tmp_path)
    assert found is not None and found.name == "b.md"


def test_find_form_md_none_when_absent(tmp_path: Path):
    assert _find_form_md(tmp_path) is None


def test_should_answer_idea_only_heading_shallow():
    assert _should_answer_question(_mk_q("heading", depth=2), "idea")
    assert _should_answer_question(_mk_q("heading", depth=3), "idea")
    assert not _should_answer_question(_mk_q("heading", depth=4), "idea")
    assert not _should_answer_question(_mk_q("table_row_label"), "idea")
    assert not _should_answer_question(_mk_q("table_cell_answer_slot"), "idea")


def test_should_answer_structure_heading_plus_row_label():
    assert _should_answer_question(_mk_q("heading", depth=5), "structure")
    assert _should_answer_question(_mk_q("table_row_label"), "structure")
    assert not _should_answer_question(_mk_q("table_cell_answer_slot"), "structure")
    assert not _should_answer_question(_mk_q("table_header"), "structure")


def test_should_answer_spec_includes_headers():
    assert _should_answer_question(_mk_q("table_header"), "spec")
    assert _should_answer_question(_mk_q("table_header"), "proposal")
    assert _should_answer_question(_mk_q("table_header"), "experiment-plan")


def test_question_goal_includes_stage_topic_path():
    q = _mk_q("heading", title="상용화 대상 명칭", path="Section 0 > 표 6 > 상용화 대상 명칭")
    g = _question_goal(q, user_input="LOEKAL AI", stage="idea")
    assert "[idea]" in g
    assert "LOEKAL AI" in g
    assert "상용화 대상 명칭" in g
    # path 힌트 포함
    assert "표 6" in g or "상용화 대상 명칭" in g


def test_question_goal_empty_input():
    q = _mk_q("heading", title="t1")
    g = _question_goal(q, user_input="", stage="spec")
    assert "[spec]" in g and "t1" in g


def test_resolve_stage_dir_matches_existing(tmp_path: Path):
    (tmp_path / "02-debate").mkdir()
    (tmp_path / "03-structure-custom").mkdir()
    assert _resolve_stage_dir(tmp_path, "debate").name == "02-debate"
    # 사용자가 suffix 커스텀한 경우도 매칭
    assert _resolve_stage_dir(tmp_path, "structure").name == "03-structure-custom"


def test_resolve_stage_dir_creates_nn_when_missing(tmp_path: Path):
    assert _resolve_stage_dir(tmp_path, "idea").name == "01-idea"
    assert _resolve_stage_dir(tmp_path, "proposal").name == "07-proposal"


def test_resolve_stage_dir_unknown_stage_uses_nn_placeholder(tmp_path: Path):
    assert _resolve_stage_dir(tmp_path, "xyz").name == "NN-xyz"
