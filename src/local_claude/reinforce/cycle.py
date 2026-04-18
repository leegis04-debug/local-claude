"""한 번의 P-Reinforce 사이클: analyze → findings → suggestions → (선택) apply.

apply 모드에서 자동 적용되는 것은 오직 **구조적·안전**한 변경만:
- threshold_tune: 정책 파일 `thresholds.md` 에 제안 기록 (실제 값 변경은 사용자 승인)
- investigate: log only

prompt_rewrite / policy_update 는 로그만 남기고 사용자에게 제시 — LLM 이 프로덕션 코드를 직접 덮어쓰는 건 막는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..perf import logger as perf_logger
from ..perf.schema import PerfEvent
from . import analyzer, findings as findings_mod, policy, suggestions


@dataclass
class CycleResult:
    started_at: str
    analyzed_events: int
    findings: list[dict[str, Any]] = field(default_factory=list)
    suggestions_source: str = "rule"
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    apply_mode: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "analyzed_events": self.analyzed_events,
            "findings": self.findings,
            "suggestions_source": self.suggestions_source,
            "suggestions": self.suggestions,
            "applied": self.applied,
            "apply_mode": self.apply_mode,
        }


def _record_suggestion_as_policy_note(
    sug: suggestions.Suggestion,
) -> tuple[str, int]:
    """제안을 policy `reinforce-suggestions.md` 에 append — revision 으로 이력 유지."""
    name = "reinforce-suggestions"
    existing = policy.show(name) or "# P-Reinforce 제안 이력\n\n"
    ts = datetime.now().astimezone().isoformat()
    entry = (
        f"## {ts} · {sug.finding_id} · {sug.change_type}\n"
        f"- target: {sug.target}\n"
        f"- detail: {sug.detail}\n"
        f"- rationale: {sug.rationale}\n\n"
    )
    new_content = existing + entry
    rev = policy.set_policy(name, new_content)
    return name, rev


def run(
    project_dir: Path | str | None = None,
    *,
    days: int = 7,
    use_llm: bool = True,
    apply_mode: bool = False,
    llm_timeout_s: int = 60,
    thresholds: dict[str, float] | None = None,
) -> CycleResult:
    result = CycleResult(
        started_at=datetime.now().astimezone().isoformat(),
        analyzed_events=0,
        apply_mode=apply_mode,
    )

    # 1) analyze
    report = analyzer.analyze(project_dir, days=days)
    result.analyzed_events = report.total_events

    # 2) findings
    finding_list = findings_mod.derive(report, thresholds=thresholds)
    result.findings = [f.to_dict() for f in finding_list]

    # 3) suggestions
    suggested = suggestions.generate(
        finding_list, use_llm=use_llm, timeout_s=llm_timeout_s
    )
    result.suggestions_source = suggested.source
    result.suggestions = [s.to_dict() for s in suggested.suggestions]

    # 4) apply (옵션) — 구조적 변경만.
    if apply_mode:
        for sug in suggested.suggestions:
            if sug.change_type in {"threshold_tune", "policy_update"}:
                try:
                    name, rev = _record_suggestion_as_policy_note(sug)
                    result.applied.append(
                        {
                            "finding_id": sug.finding_id,
                            "change_type": sug.change_type,
                            "recorded_in": name,
                            "backup_rev": rev,
                        }
                    )
                except Exception as exc:
                    result.applied.append(
                        {"finding_id": sug.finding_id, "error": str(exc)}
                    )

    # 5) 메타 — 이 사이클 자체를 perf 에 기록.
    try:
        perf_logger.log(
            PerfEvent(
                action="reinforce.cycle",
                duration_ms=0,
                exit_code=0,
                extra={
                    "events": report.total_events,
                    "findings": len(finding_list),
                    "applied": len(result.applied),
                    "source": suggested.source,
                },
            ),
            project_dir=project_dir,
        )
    except Exception:
        pass

    return result


def export_json(result: CycleResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
