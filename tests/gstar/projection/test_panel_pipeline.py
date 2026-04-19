"""svrr pipeline panel 모드 — `_project_panel` 동작 검증.

외부 Ollama 없이 동작해야 함. project_section 을 mock 해서 호출 수·section_name·
draft 실패·fallback 경로를 검증.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gstar.projection.pipeline_e import PipelineResult, _project_panel
from gstar.projection.projector import ProjectionInput, ProjectorOutput, SectionSpec


def _mk_input(stage: str, facts=None):
    return ProjectionInput(
        goal="goal",
        track="proposal",
        facts=facts or [{"node_id": "a", "text": "t"}],
        section=SectionSpec(name=stage, instruction="지시", target_tokens=200),
    )


def _mk_rep(stage: str):
    return PipelineResult(mode="svrr", goal="goal", track="proposal", section=stage)


def _ok_output(section_name: str, text: str = "drafted"):
    return ProjectorOutput(section_name=section_name, text=text, used_fact_ids=[], model="mock")


def test_panel_fallback_when_stage_has_no_panel():
    """debate stage 는 panel 정의 비어 있어 단일 project_section 폴백."""
    rep = _mk_rep("debate")
    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.return_value = _ok_output("debate", "단일 결과")
        out = _project_panel(_mk_input("debate"), track_name="proposal", rep=rep)
    assert out.text == "단일 결과"
    assert m.call_count == 1
    assert any("panel 미정의" in w for w in rep.warnings)


def test_panel_idea_4_personas_plus_integrator():
    """idea = 4 persona + 1 integrator → project_section 5 회 호출."""
    rep = _mk_rep("idea")
    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.side_effect = lambda x: _ok_output(x.section.name, f"text-{x.section.name}")
        out = _project_panel(_mk_input("idea"), track_name="proposal", rep=rep)
    assert m.call_count == 5
    assert out.section_name == "idea"  # integrator 후 원래 이름으로 복원


def test_panel_proposal_5_personas_plus_integrator():
    """proposal = 5 persona + 1 integrator = 6 회."""
    rep = _mk_rep("proposal")
    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.side_effect = lambda x: _ok_output(x.section.name, f"t-{x.section.name}")
        _project_panel(_mk_input("proposal"), track_name="proposal", rep=rep)
    assert m.call_count == 6


def test_panel_filters_failed_drafts():
    """draft 일부 실패 시 남은 성공분만 integrator 로 전달. 호출 수는 실패 포함."""
    rep = _mk_rep("idea")

    def side(x):
        role = x.section.name.split("::")[-1] if "::" in x.section.name else x.section.name
        if role == "strategist":
            return _ok_output(x.section.name, "(Projector 호출 실패: fake)")
        return _ok_output(x.section.name, f"ok-{role}")

    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.side_effect = side
        _project_panel(_mk_input("idea"), track_name="proposal", rep=rep)
    # 4 persona + 1 integrator = 5. strategist 실패는 warning 으로
    assert m.call_count == 5
    assert any("strategist" in w and "실패" in w for w in rep.warnings)


def test_panel_all_failed_falls_back_to_single():
    """모든 persona 실패 → 단일 project_section 폴백 한 번 더."""
    rep = _mk_rep("idea")
    call_log = []

    def side(x):
        call_log.append(x.section.name)
        if "::" in x.section.name:
            return _ok_output(x.section.name, "(Projector 호출 실패: dead)")
        return _ok_output(x.section.name, "fallback-ok")

    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.side_effect = side
        out = _project_panel(_mk_input("idea"), track_name="proposal", rep=rep)
    # 4 panel 시도 실패 + fallback 1 = 5
    assert m.call_count == 5
    assert out.text == "fallback-ok"
    assert any("모든 persona 실패" in w for w in rep.warnings)


def test_panel_integrator_exception_falls_back_to_first_draft():
    """integrator 예외 시 첫 draft 로 fallback. section_name 은 원래 stage 유지."""
    rep = _mk_rep("idea")

    def side(x):
        if "integrated" in x.section.name:
            raise RuntimeError("integrator dead")
        return _ok_output(x.section.name, f"draft-{x.section.name}")

    with patch("gstar.projection.pipeline_e.project_section") as m:
        m.side_effect = side
        out = _project_panel(_mk_input("idea"), track_name="proposal", rep=rep)
    # 4 persona + 1 integrator(실패) = 5
    assert m.call_count == 5
    assert out.section_name == "idea"
    assert out.text.startswith("draft-")
    assert any("integrator 실패" in w for w in rep.warnings)
