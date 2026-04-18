from __future__ import annotations

from local_claude.reinforce.analyzer import ActionStat, AnalyzerReport
from local_claude.reinforce.findings import derive


def _report(**actions) -> AnalyzerReport:
    report = AnalyzerReport(days=7)
    for name, stat_kwargs in actions.items():
        stat = ActionStat(action=name, **stat_kwargs)
        report.by_action[name] = stat
        report.total_events += stat.count
        report.total_failures += stat.failures
    return report


def test_high_fail_rate_detected() -> None:
    report = _report(validate={"count": 10, "failures": 6})
    finds = derive(report)
    titles = [f.title for f in finds]
    assert any("validate" in t and "실패율" in t for t in titles)
    sev = next(f.severity for f in finds if "validate" in f.title and "실패율" in f.title)
    assert sev == "high"


def test_slow_action_flagged() -> None:
    report = _report(ask_deep={"count": 5, "total_duration_ms": 5 * 65_000})
    finds = derive(report)
    titles = [f.title for f in finds]
    assert any("ask_deep" in t and "ms" in t for t in titles)
    sev = next(f.severity for f in finds if "ask_deep" in f.title and "ms" in f.title)
    assert sev == "high"


def test_flaky_run_test_flagged_as_test_area() -> None:
    report = _report(run_test={"count": 5, "total_retries": 6})  # 평균 1.2
    finds = derive(report)
    test_find = next((f for f in finds if f.action == "run_test" and "재시도" in f.title), None)
    assert test_find is not None
    assert test_find.area == "test"


def test_validation_fail_triggers_prompt_area() -> None:
    report = _report(jw_idea={"count": 10, "validation_fails": 3})
    # 약간의 duration 설정
    report.by_action["jw_idea"].total_duration_ms = 100
    finds = derive(report)
    v_find = next((f for f in finds if f.action == "jw_idea" and "validation" in f.title), None)
    assert v_find is not None
    assert v_find.area == "prompt"


def test_overall_fail_rate_emits_policy_finding() -> None:
    report = _report(a={"count": 10, "failures": 5}, b={"count": 10, "failures": 0})
    finds = derive(report)
    overall = next((f for f in finds if f.area == "policy" and "전체 실패율" in f.title), None)
    assert overall is not None
    assert overall.severity == "high"


def test_low_count_actions_ignored() -> None:
    # count=1 은 통계적으로 무시
    report = _report(once={"count": 1, "failures": 1})
    finds = derive(report)
    assert all(f.action != "once" for f in finds if f.action)


def test_no_findings_for_clean_report() -> None:
    report = _report(good={"count": 20, "failures": 0, "total_duration_ms": 200})
    finds = derive(report)
    assert finds == []
