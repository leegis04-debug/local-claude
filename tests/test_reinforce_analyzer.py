from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from local_claude.perf import logger as perf_logger
from local_claude.perf.schema import PerfEvent
from local_claude.reinforce import analyzer


def _write_event(project: Path, **kwargs) -> None:
    perf_logger.log(PerfEvent(**kwargs), project_dir=project)


def test_analyze_empty_returns_zero(tmp_path: Path) -> None:
    report = analyzer.analyze(tmp_path)
    assert report.total_events == 0
    assert report.by_action == {}
    assert report.slowest == []


def test_analyze_aggregates_by_action(tmp_path: Path) -> None:
    _write_event(tmp_path, action="jw idea", duration_ms=1000, exit_code=0, model="gemma")
    _write_event(tmp_path, action="jw idea", duration_ms=2000, exit_code=0, model="gemma")
    _write_event(tmp_path, action="jw spec", duration_ms=30000, exit_code=1)
    _write_event(tmp_path, action="jw spec", duration_ms=40000, exit_code=1)

    report = analyzer.analyze(tmp_path)
    assert report.total_events == 4
    assert report.total_failures == 2
    assert abs(report.fail_rate - 0.5) < 1e-9

    idea = report.by_action["jw idea"]
    assert idea.count == 2
    assert idea.fail_rate == 0.0
    assert idea.avg_duration_ms == 1500
    assert idea.models == {"gemma": 2}

    spec = report.by_action["jw spec"]
    assert spec.count == 2
    assert spec.fail_rate == 1.0
    assert spec.avg_duration_ms == 35000


def test_analyze_rankings(tmp_path: Path) -> None:
    # 느린 action + 실패 action 분리
    for _ in range(3):
        _write_event(tmp_path, action="search_rag", duration_ms=500, exit_code=0)
    for _ in range(3):
        _write_event(tmp_path, action="ask_deep", duration_ms=45000, exit_code=0)
    for _ in range(4):
        _write_event(tmp_path, action="validate", duration_ms=100, exit_code=1)

    report = analyzer.analyze(tmp_path)
    assert report.slowest[0]["action"] == "ask_deep"
    assert report.most_failing[0]["action"] == "validate"


def test_analyze_respects_days_window(tmp_path: Path) -> None:
    # 오늘 로그만
    _write_event(tmp_path, action="today", duration_ms=100)
    # 10일 전 로그 수동 작성
    old_day = datetime.now() - timedelta(days=10)
    old_path = perf_logger.log_path(tmp_path, when=old_day)
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text('{"ts":"old","action":"old_action","duration_ms":999,"exit_code":0}\n')

    report_7d = analyzer.analyze(tmp_path, days=7)
    assert "today" in report_7d.by_action
    assert "old_action" not in report_7d.by_action

    report_14d = analyzer.analyze(tmp_path, days=14)
    assert "old_action" in report_14d.by_action


def test_analyze_min_count_filters_rankings(tmp_path: Path) -> None:
    # 1회만 발생한 action 은 랭킹에서 제외 (min_count=2)
    _write_event(tmp_path, action="once", duration_ms=100000)
    _write_event(tmp_path, action="multi", duration_ms=50)
    _write_event(tmp_path, action="multi", duration_ms=60)

    report = analyzer.analyze(tmp_path, min_count_for_ranking=2)
    slow_actions = [s["action"] for s in report.slowest]
    assert "once" not in slow_actions
