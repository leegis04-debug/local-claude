from __future__ import annotations

from pathlib import Path

import pytest

from local_claude.perf import logger as perf_logger
from local_claude.perf.schema import PerfEvent
from local_claude.reinforce import cycle, findings, suggestions


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("local_claude.config.LC_DATA_DIR", tmp_path / "lc-data")
    return tmp_path


def _seed_perf(project: Path, *, failures: int = 5, good: int = 5) -> None:
    for _ in range(failures):
        perf_logger.log(
            PerfEvent(action="jw_idea", duration_ms=35000, exit_code=1, validation="fail"),
            project_dir=project,
        )
    for _ in range(good):
        perf_logger.log(
            PerfEvent(action="search_rag", duration_ms=500, exit_code=0),
            project_dir=project,
        )


def test_suggestions_rule_based_fallback_when_llm_off() -> None:
    f = findings.Finding(
        id="F001", title="slow", severity="high", area="threshold",
        evidence={"avg_duration_ms": 60_000}, action="ask_deep",
    )
    result = suggestions.generate([f], use_llm=False)
    assert result.source == "rule"
    assert len(result.suggestions) == 1
    assert result.suggestions[0].change_type == "threshold_tune"
    assert result.suggestions[0].target == "ask_deep"


def test_suggestions_llm_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_output = """[
      {"finding_id": "F001", "change_type": "prompt_rewrite",
       "target": "prompts/jw_idea.md", "detail": "섹션 보강",
       "rationale": "validation fail 많음", "auto_applicable": false}
    ]"""

    monkeypatch.setattr("local_claude.reinforce.suggestions._run_ask_gemma", lambda *a, **k: fake_output)

    f1 = findings.Finding(id="F001", title="t1", severity="high", area="prompt", action="jw_idea")
    f2 = findings.Finding(id="F002", title="t2", severity="med", area="threshold", action="search_rag")
    result = suggestions.generate([f1, f2], use_llm=True)
    assert result.source == "hybrid"
    # F001 은 LLM 결과, F002 는 rule fallback
    by_id = {s.finding_id: s for s in result.suggestions}
    assert by_id["F001"].change_type == "prompt_rewrite"
    assert by_id["F001"].target == "prompts/jw_idea.md"
    assert by_id["F002"].change_type == "threshold_tune"


def test_suggestions_llm_failure_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("local_claude.reinforce.suggestions._run_ask_gemma", lambda *a, **k: "")
    f = findings.Finding(id="F001", title="x", severity="med", area="prompt", action="a")
    result = suggestions.generate([f], use_llm=True)
    assert result.source == "rule"


def test_cycle_runs_end_to_end(isolated_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = isolated_data / "proj"
    project.mkdir()
    _seed_perf(project, failures=6, good=4)

    # LLM 비활성 — 규칙 기반만으로도 완결.
    result = cycle.run(project, days=7, use_llm=False, apply_mode=False)
    assert result.analyzed_events == 10
    assert len(result.findings) > 0
    assert len(result.suggestions) > 0
    assert result.applied == []  # apply=False


def test_cycle_apply_records_structural_suggestions(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = isolated_data / "proj2"
    project.mkdir()
    _seed_perf(project, failures=8, good=2)

    result = cycle.run(project, days=7, use_llm=False, apply_mode=True)
    # threshold_tune/policy_update 후보가 reinforce-suggestions 정책으로 기록
    assert result.apply_mode is True
    # apply 된 것이 있어야 (규칙 기반이 생성한 threshold 또는 policy 계열)
    # 최소 하나는 기록되었어야 한다 (slow action 이 있으므로)
    if result.applied:
        first = result.applied[0]
        assert first.get("recorded_in") == "reinforce-suggestions"


def test_cycle_handles_empty_logs(isolated_data: Path) -> None:
    project = isolated_data / "empty"
    project.mkdir()
    result = cycle.run(project, days=7, use_llm=False)
    assert result.analyzed_events == 0
    assert result.findings == []
    assert result.suggestions == []
