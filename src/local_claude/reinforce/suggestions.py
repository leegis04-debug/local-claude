"""Findings → 구체 개선안 생성 (ask-gemma 브릿지).

LLM 이 없거나 실패하면 **규칙 기반 fallback** 으로 최소한의 suggestion 제공.
즉 외부 의존 없이도 동작 — LLM 은 품질 업그레이드용.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import config
from .findings import Finding


_CHANGE_TYPES = {"prompt_rewrite", "policy_update", "threshold_tune", "add_check", "investigate"}


@dataclass
class Suggestion:
    finding_id: str
    change_type: str  # prompt_rewrite | policy_update | threshold_tune | add_check | investigate
    target: str  # policy name / action / 파일 경로 등
    detail: str  # 구체 제안
    rationale: str = ""
    auto_applicable: bool = False  # cycle.py --apply 시 자동 적용 가능한가

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── rule-based fallback ─────────────────────────────────────────────────────

def _rule_based(finding: Finding) -> Suggestion:
    area = finding.area
    action = finding.action or "unknown"

    if area == "threshold":
        return Suggestion(
            finding_id=finding.id,
            change_type="threshold_tune",
            target=action,
            detail=f"{action} 의 timeout/top_k 를 조정해 지연 감소 시도",
            rationale=f"avg_duration 이 임계 초과: {finding.evidence}",
            auto_applicable=False,
        )
    if area == "test":
        return Suggestion(
            finding_id=finding.id,
            change_type="investigate",
            target=action,
            detail=f"{action} flaky 재시도 패턴 — 테스트 환경 의존성·리소스 경쟁 조사",
            rationale=f"평균 재시도율 상승: {finding.evidence}",
            auto_applicable=False,
        )
    if area == "prompt":
        return Suggestion(
            finding_id=finding.id,
            change_type="prompt_rewrite",
            target=f"prompts/{action}.md",
            detail=f"{action} 프롬프트에 누락 섹션 보강 또는 검증 기준 명시화",
            rationale=f"validation/실패율 높음: {finding.evidence}",
            auto_applicable=False,
        )
    # 기본
    return Suggestion(
        finding_id=finding.id,
        change_type="investigate",
        target=action,
        detail=finding.title,
        rationale=f"area={area}, evidence={finding.evidence}",
    )


# ── LLM-based (ask-gemma) ───────────────────────────────────────────────────

_SUGGEST_PROMPT = """너는 시스템 자기개선 분석가다. 아래 findings 에 대해 각각
**실행 가능한 개선안**을 JSON 배열로만 반환하라. 설명·코드블록 금지.

각 suggestion 스키마:
{"finding_id": "Fxxx",
 "change_type": "prompt_rewrite|policy_update|threshold_tune|add_check|investigate",
 "target": "<정책/프롬프트 이름 또는 action>",
 "detail": "구체 행동 1줄",
 "rationale": "왜 이 변경인가 1줄",
 "auto_applicable": false}

findings:
{findings}
"""


def _run_ask_gemma(prompt: str, timeout: int) -> str:
    bin_path = shutil.which(config.ASK_GEMMA_BIN) or config.ASK_GEMMA_BIN
    try:
        result = subprocess.run(
            [bin_path],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _parse_llm_suggestions(raw: str, known_ids: set[str]) -> list[Suggestion]:
    if not raw:
        return []
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except Exception:
        return []
    if not isinstance(items, list):
        return []

    out: list[Suggestion] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("finding_id") or "").strip()
        if fid not in known_ids:
            continue
        change_type = str(item.get("change_type") or "investigate")
        if change_type not in _CHANGE_TYPES:
            change_type = "investigate"
        out.append(
            Suggestion(
                finding_id=fid,
                change_type=change_type,
                target=str(item.get("target") or ""),
                detail=str(item.get("detail") or ""),
                rationale=str(item.get("rationale") or ""),
                auto_applicable=bool(item.get("auto_applicable", False)),
            )
        )
    return out


@dataclass
class SuggestionsResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    source: str = "rule"  # "rule" | "llm" | "hybrid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "suggestions": [s.to_dict() for s in self.suggestions],
        }


def generate(
    findings: list[Finding],
    *,
    use_llm: bool = True,
    timeout_s: int = 60,
) -> SuggestionsResult:
    if not findings:
        return SuggestionsResult()

    # 기본은 규칙 기반 — 실패 안전.
    rule_based = [_rule_based(f) for f in findings]

    if not use_llm:
        return SuggestionsResult(suggestions=rule_based, source="rule")

    finding_blob = json.dumps(
        [f.to_dict() for f in findings], ensure_ascii=False, indent=2
    )
    # 프롬프트 안에 JSON 예시 중괄호가 있어 .format() 대신 replace 사용.
    prompt_text = _SUGGEST_PROMPT.replace("{findings}", finding_blob)
    raw = _run_ask_gemma(prompt_text, timeout=timeout_s)
    llm_suggestions = _parse_llm_suggestions(raw, known_ids={f.id for f in findings})
    if not llm_suggestions:
        return SuggestionsResult(suggestions=rule_based, source="rule")

    # LLM 이 커버한 finding 은 그 결과로, 아니면 rule fallback.
    covered = {s.finding_id for s in llm_suggestions}
    merged: list[Suggestion] = list(llm_suggestions)
    for s in rule_based:
        if s.finding_id not in covered:
            merged.append(s)
    return SuggestionsResult(suggestions=merged, source="hybrid")
