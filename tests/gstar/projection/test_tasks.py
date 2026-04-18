"""TaskDefinition 플러그인 4개 구조 테스트."""

from __future__ import annotations

import pytest

from gstar.projection.tasks import get_task, list_tasks
from gstar.projection.template import SectionSpec


def test_list_tasks_registers_all_four():
    names = set(list_tasks())
    assert names == {"proposal", "research", "coding", "document"}


@pytest.mark.parametrize("name", ["proposal", "research", "coding", "document"])
def test_each_task_exposes_stages(name):
    task = get_task(name)
    assert task.name == name
    assert len(task.stages) >= 3
    for stage in task.stages:
        role = task.stage_role(stage)
        assert role.section_schema
        schema = task.section_schema(stage)
        assert all(isinstance(s, SectionSpec) for s in schema)


def test_proposal_coherence_rules_for_proposal_stage():
    task = get_task("proposal")
    rules = task.coherence_rules("proposal")
    assert rules
    assert all(r.applies_stages == ["proposal"] for r in rules)


def test_research_has_lab_tracker_hook():
    task = get_task("research")
    hooks = task.ingest_hooks()
    assert any(h.name == "lab_tracker" for h in hooks)


def test_coding_implement_outputs_code_format():
    task = get_task("coding")
    assert task.output_format("implement") == "code"
    assert task.output_format("plan") == "markdown"


def test_unknown_track_raises():
    with pytest.raises(KeyError):
        get_task("does_not_exist")


def test_document_stages_match_schema():
    task = get_task("document")
    for stage in task.stages:
        schema = task.section_schema(stage)
        assert schema, f"stage {stage} has empty schema"
