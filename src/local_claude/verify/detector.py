"""Hallucination detector — answer 전체에 대해 claim 별 verdict 를 종합.

1. claims.extract_claims(answer) 로 검증 대상 추출
2. 각 claim 에 대해 cross_check 호출
3. aggregate → 전체 verdict + score
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import claims as claims_mod
from .cross_check import CrossCheckResult, cross_check
from .reranker import Document


@dataclass
class HallucinationReport:
    overall: str  # "supported" | "partial" | "unsupported" | "no_claims"
    score: float  # supported 비율 (partial 은 0.5)
    total_claims: int
    checks: list[CrossCheckResult] = field(default_factory=list)
    mode: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "score": round(self.score, 3),
            "total_claims": self.total_claims,
            "mode": self.mode,
            "checks": [c.to_dict() for c in self.checks],
        }


def _aggregate(checks: list[CrossCheckResult]) -> tuple[str, float]:
    if not checks:
        return "no_claims", 0.0
    weight = 0.0
    for c in checks:
        if c.verdict == "supported":
            weight += 1.0
        elif c.verdict == "partial":
            weight += 0.5
    score = weight / len(checks)
    if score >= 0.75:
        overall = "supported"
    elif score >= 0.4:
        overall = "partial"
    else:
        overall = "unsupported"
    return overall, score


def detect(
    answer: str,
    evidences: Iterable[Document],
    *,
    mode: str = "local",
    min_claim_score: float = 1.0,
    max_claims: int | None = 20,
    high: float = 0.55,
    low: float = 0.28,
    timeout_s: int = 30,
    deep: bool = False,
) -> HallucinationReport:
    ev_list = list(evidences)
    extracted = claims_mod.extract_claims(
        answer, min_score=min_claim_score, max_claims=max_claims
    )
    checks: list[CrossCheckResult] = []
    for c in extracted:
        result = cross_check(
            c.text,
            ev_list,
            mode=mode,
            high=high,
            low=low,
            timeout_s=timeout_s,
            deep=deep,
        )
        checks.append(result)
    overall, score = _aggregate(checks)
    return HallucinationReport(
        overall=overall,
        score=score,
        total_claims=len(checks),
        checks=checks,
        mode=mode,
    )
