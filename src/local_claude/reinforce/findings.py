"""AnalyzerReport → 구조화된 Finding 리스트.

규칙 기반 판별 — 외부 LLM 호출 없이 명시 임계로만 산출.
Finding: {id, title, severity, evidence, area, action}.

area 는 suggestion 단계에서 "어디를 고쳐야 할까?" 힌트로 쓰인다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .analyzer import AnalyzerReport

# 임계값 — 필요 시 정책 파일에서 오버라이드 가능 (Phase 6 cycle.py 에서 처리).
DEFAULT_THRESHOLDS: dict[str, float] = {
    "high_fail_rate": 0.3,
    "med_fail_rate": 0.15,
    "slow_action_ms": 30_000,
    "very_slow_action_ms": 60_000,
    "high_retry_rate": 1.0,  # 평균 재시도 1회 이상 = flaky 의심
    "validation_fail_rate": 0.2,
    "overall_fail_rate_high": 0.2,
}


@dataclass
class Finding:
    id: str
    title: str
    severity: str  # "high" | "med" | "low"
    area: str  # "prompt" | "policy" | "threshold" | "infra" | "test"
    evidence: dict[str, Any] = field(default_factory=dict)
    action: str | None = None  # 관련 action 이름 (있으면)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sev_from_fail_rate(rate: float, thresholds: dict[str, float]) -> str | None:
    if rate >= thresholds["high_fail_rate"]:
        return "high"
    if rate >= thresholds["med_fail_rate"]:
        return "med"
    return None


def _sev_from_duration(ms: float, thresholds: dict[str, float]) -> str | None:
    if ms >= thresholds["very_slow_action_ms"]:
        return "high"
    if ms >= thresholds["slow_action_ms"]:
        return "med"
    return None


def derive(
    report: AnalyzerReport,
    *,
    thresholds: dict[str, float] | None = None,
) -> list[Finding]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    findings: list[Finding] = []
    seq = 0

    def _next_id() -> str:
        nonlocal seq
        seq += 1
        return f"F{seq:03d}"

    # 전체 fail_rate
    if report.fail_rate >= th["overall_fail_rate_high"]:
        findings.append(
            Finding(
                id=_next_id(),
                title=f"전체 실패율 {report.fail_rate:.1%} — 임계({th['overall_fail_rate_high']:.0%}) 초과",
                severity="high",
                area="policy",
                evidence={"fail_rate": report.fail_rate, "events": report.total_events},
            )
        )

    # action 별 검사
    for stat in report.by_action.values():
        if stat.count < 2:
            continue

        sev = _sev_from_fail_rate(stat.fail_rate, th)
        if sev is not None:
            findings.append(
                Finding(
                    id=_next_id(),
                    title=f"{stat.action} 실패율 {stat.fail_rate:.1%} — 개선 필요",
                    severity=sev,
                    area="prompt" if stat.action.startswith(("jw", "re")) else "policy",
                    evidence={
                        "action": stat.action,
                        "fail_rate": stat.fail_rate,
                        "count": stat.count,
                    },
                    action=stat.action,
                )
            )

        sev = _sev_from_duration(stat.avg_duration_ms, th)
        if sev is not None:
            findings.append(
                Finding(
                    id=_next_id(),
                    title=f"{stat.action} 평균 {stat.avg_duration_ms:.0f}ms — 느림",
                    severity=sev,
                    area="threshold",
                    evidence={
                        "action": stat.action,
                        "avg_duration_ms": stat.avg_duration_ms,
                        "count": stat.count,
                    },
                    action=stat.action,
                )
            )

        if stat.avg_retries >= th["high_retry_rate"]:
            findings.append(
                Finding(
                    id=_next_id(),
                    title=f"{stat.action} 평균 재시도 {stat.avg_retries:.1f}회 — flaky 의심",
                    severity="med",
                    area="test" if stat.action == "run_test" else "prompt",
                    evidence={
                        "action": stat.action,
                        "avg_retries": stat.avg_retries,
                        "count": stat.count,
                    },
                    action=stat.action,
                )
            )

        validation_rate = stat.validation_fails / stat.count
        if validation_rate >= th["validation_fail_rate"]:
            findings.append(
                Finding(
                    id=_next_id(),
                    title=f"{stat.action} validation fail {validation_rate:.0%} — 검증 기준·프롬프트 재검토",
                    severity="med",
                    area="prompt",
                    evidence={
                        "action": stat.action,
                        "validation_fails": stat.validation_fails,
                        "count": stat.count,
                    },
                    action=stat.action,
                )
            )

    return findings
