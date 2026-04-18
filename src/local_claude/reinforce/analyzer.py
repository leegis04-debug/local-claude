"""perf 로그 집계 — 기간별 action 통계.

입력: {프로젝트}/.perf/log-YYYY-MM-DD.jsonl (Phase 1 스키마).
출력: AnalyzerReport — action별 count/fail_rate/avg_duration/retries,
      느린 top N, 실패 top N, 모델별 분포.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..perf import logger as perf_logger


@dataclass
class ActionStat:
    action: str
    count: int = 0
    failures: int = 0
    total_duration_ms: int = 0
    total_retries: int = 0
    models: dict[str, int] = field(default_factory=dict)
    validation_fails: int = 0

    @property
    def fail_rate(self) -> float:
        return self.failures / self.count if self.count else 0.0

    @property
    def avg_duration_ms(self) -> float:
        return self.total_duration_ms / self.count if self.count else 0.0

    @property
    def avg_retries(self) -> float:
        return self.total_retries / self.count if self.count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "count": self.count,
            "failures": self.failures,
            "fail_rate": round(self.fail_rate, 3),
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "avg_retries": round(self.avg_retries, 2),
            "validation_fails": self.validation_fails,
            "models": dict(self.models),
        }


@dataclass
class AnalyzerReport:
    days: int
    total_events: int = 0
    total_failures: int = 0
    by_action: dict[str, ActionStat] = field(default_factory=dict)
    slowest: list[dict[str, Any]] = field(default_factory=list)
    most_failing: list[dict[str, Any]] = field(default_factory=list)
    most_retried: list[dict[str, Any]] = field(default_factory=list)
    model_usage: dict[str, int] = field(default_factory=dict)

    @property
    def fail_rate(self) -> float:
        return self.total_failures / self.total_events if self.total_events else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "total_events": self.total_events,
            "total_failures": self.total_failures,
            "fail_rate": round(self.fail_rate, 3),
            "by_action": {k: v.to_dict() for k, v in self.by_action.items()},
            "slowest": self.slowest,
            "most_failing": self.most_failing,
            "most_retried": self.most_retried,
            "model_usage": dict(self.model_usage),
        }


def _iter_events(
    project_dir: Path | None,
    days: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for i in range(days):
        day = datetime.now() - timedelta(days=i)
        path = perf_logger.log_path(project_dir, when=day)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def analyze(
    project_dir: Path | str | None = None,
    *,
    days: int = 7,
    top_n: int = 5,
    min_count_for_ranking: int = 2,
) -> AnalyzerReport:
    root = Path(project_dir).resolve() if project_dir else Path.cwd()
    events = _iter_events(root, days)
    report = AnalyzerReport(days=days, total_events=len(events))

    stats: dict[str, ActionStat] = defaultdict(lambda: ActionStat(action=""))
    model_usage: dict[str, int] = defaultdict(int)

    for event in events:
        action = str(event.get("action") or "")
        if not action:
            continue
        stat = stats[action]
        stat.action = action
        stat.count += 1

        exit_code = int(event.get("exit_code", 0))
        if exit_code != 0:
            stat.failures += 1
            report.total_failures += 1

        stat.total_duration_ms += int(event.get("duration_ms", 0))
        stat.total_retries += int(event.get("retries", 0))

        if event.get("validation") == "fail":
            stat.validation_fails += 1

        model = event.get("model")
        if model:
            stat.models[str(model)] = stat.models.get(str(model), 0) + 1
            model_usage[str(model)] += 1

    report.by_action = dict(stats)
    report.model_usage = dict(model_usage)

    # 랭킹 — 최소 호출 수 이상만
    eligible = [s for s in stats.values() if s.count >= min_count_for_ranking]

    report.slowest = [
        {"action": s.action, "avg_duration_ms": round(s.avg_duration_ms, 1), "count": s.count}
        for s in sorted(eligible, key=lambda x: x.avg_duration_ms, reverse=True)[:top_n]
        if s.avg_duration_ms > 0
    ]
    report.most_failing = [
        {"action": s.action, "fail_rate": round(s.fail_rate, 3), "count": s.count, "failures": s.failures}
        for s in sorted(eligible, key=lambda x: x.fail_rate, reverse=True)[:top_n]
        if s.fail_rate > 0
    ]
    report.most_retried = [
        {"action": s.action, "avg_retries": round(s.avg_retries, 2), "count": s.count}
        for s in sorted(eligible, key=lambda x: x.avg_retries, reverse=True)[:top_n]
        if s.avg_retries > 0
    ]

    return report
