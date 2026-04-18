"""Claim × Evidence 교차 검증 — hallucination 탐지의 핵심 단위.

로컬 모드(기본): 토큰 containment 비율로 verdict 산출. zero-dep.
LLM 모드: ask-gemma 에 "이 claim 이 근거에 지지되는가?" 질의.

verdict:
- supported     : 근거 중 최소 1개가 강하게 지지 (containment ≥ high)
- partial       : medium 지지만 존재
- unsupported   : 어떤 근거도 부족
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable

from .. import config
from ._tokenize import containment
from .reranker import Document, _doc_text

_Verdict = str  # "supported" | "partial" | "unsupported"

SUPPORT_HIGH = 0.55
SUPPORT_LOW = 0.28


@dataclass
class EvidenceMatch:
    evidence_index: int
    source: str | None
    score: float  # containment 비율

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_index": self.evidence_index,
            "source": self.source,
            "score": round(self.score, 3),
        }


@dataclass
class CrossCheckResult:
    claim: str
    verdict: _Verdict
    best_score: float
    matches: list[EvidenceMatch] = field(default_factory=list)
    mode: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "best_score": round(self.best_score, 3),
            "matches": [m.to_dict() for m in self.matches],
            "mode": self.mode,
        }


# ── 로컬 모드 ────────────────────────────────────────────────────────────────

def _cross_check_local(
    claim: str,
    evidences: list[Document],
    *,
    high: float,
    low: float,
    top_matches: int,
) -> CrossCheckResult:
    scored: list[EvidenceMatch] = []
    best_score = 0.0
    for i, ev in enumerate(evidences):
        text = _doc_text(ev)
        score = containment(claim, text)
        if score <= 0:
            continue
        scored.append(
            EvidenceMatch(
                evidence_index=i,
                source=str(ev.get("source")) if ev.get("source") is not None else None,
                score=score,
            )
        )
        if score > best_score:
            best_score = score

    scored.sort(key=lambda m: m.score, reverse=True)
    matches = scored[:top_matches]

    if best_score >= high:
        verdict: _Verdict = "supported"
    elif best_score >= low:
        verdict = "partial"
    else:
        verdict = "unsupported"

    return CrossCheckResult(
        claim=claim, verdict=verdict, best_score=best_score, matches=matches, mode="local"
    )


# ── LLM 모드 ────────────────────────────────────────────────────────────────

_VERDICT_PROMPT = """너는 hallucination 판정기다. 아래 claim 이 evidence 들에 의해 지지되는지
**단어 한 개**로만 답하라: supported / partial / unsupported.

[claim]
{claim}

[evidences]
{evidences}
"""

_VERDICT_RE = re.compile(r"\b(supported|partial|unsupported)\b", re.IGNORECASE)


def _cross_check_llm(
    claim: str,
    evidences: list[Document],
    *,
    timeout_s: int,
    deep: bool,
) -> CrossCheckResult:
    bin_path = shutil.which(config.ASK_GEMMA_BIN) or config.ASK_GEMMA_BIN
    cmd = [bin_path]
    if deep:
        cmd.append("--deep")
    ev_blob = "\n---\n".join(f"[{i}] {_doc_text(e)[:800]}" for i, e in enumerate(evidences))
    prompt = _VERDICT_PROMPT.format(claim=claim, evidences=ev_blob)
    verdict: _Verdict = "unsupported"
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode == 0:
            match = _VERDICT_RE.search(result.stdout or "")
            if match:
                verdict = match.group(1).lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # LLM 모드에서도 매칭 증거를 함께 보고 — 로컬 containment 로 top 1~3 뽑는다.
    local_hint = _cross_check_local(
        claim, evidences, high=1.0, low=0.0, top_matches=3
    )
    return CrossCheckResult(
        claim=claim,
        verdict=verdict,
        best_score=local_hint.best_score,
        matches=local_hint.matches,
        mode="llm",
    )


# ── public ──────────────────────────────────────────────────────────────────

def cross_check(
    claim: str,
    evidences: Iterable[Document],
    *,
    mode: str = "local",
    high: float = SUPPORT_HIGH,
    low: float = SUPPORT_LOW,
    top_matches: int = 3,
    timeout_s: int = 30,
    deep: bool = False,
) -> CrossCheckResult:
    ev_list = list(evidences)
    if not claim or not claim.strip():
        return CrossCheckResult(
            claim=claim or "",
            verdict="unsupported",
            best_score=0.0,
            matches=[],
            mode=mode,
        )
    if not ev_list:
        return CrossCheckResult(
            claim=claim, verdict="unsupported", best_score=0.0, matches=[], mode=mode
        )
    if mode == "llm":
        return _cross_check_llm(claim, ev_list, timeout_s=timeout_s, deep=deep)
    return _cross_check_local(claim, ev_list, high=high, low=low, top_matches=top_matches)
