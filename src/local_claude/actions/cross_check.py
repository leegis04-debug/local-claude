"""<cross_check> — 답변 vs RAG 근거 교차 검증 (hallucination 탐지).

XML 사용 예:
  <cross_check mode="local">
    {"answer": "...", "evidences": [{"text": "..."}, ...]}
  </cross_check>

  또는 속성 + 본문(답변):
  <cross_check mode="local" evidences_json='[{"text":"..."}]'>
    답변 텍스트
  </cross_check>

payload:
  - answer 또는 _body (답변 텍스트)
  - evidences: 리스트  /  evidences_json: JSON 문자열
  - mode: local(기본) | llm
  - max_claims, min_claim_score, high, low (선택)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..verify.detector import detect
from .base import ActionResult, register


def _resolve_answer_and_evidences(payload: dict[str, Any]) -> tuple[str, list[dict]]:
    body = payload.get("_body")
    # 케이스 A: body 가 {"answer":..., "evidences":[...]} JSON
    if body:
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return (
                        str(parsed.get("answer") or payload.get("answer") or ""),
                        list(parsed.get("evidences") or []),
                    )
            except Exception:
                pass

    # 케이스 B: attribute evidences_json + body=answer
    answer = str(payload.get("answer") or body or "").strip()
    evidences: list[dict] = []
    if isinstance(payload.get("evidences"), list):
        evidences = list(payload["evidences"])
    elif payload.get("evidences_json"):
        try:
            parsed = json.loads(payload["evidences_json"])
            if isinstance(parsed, list):
                evidences = parsed
        except Exception:
            pass
    return answer, evidences


@dataclass
class CrossCheckAction:
    name: str = "cross_check"

    def execute(self, payload: dict[str, Any], **_: Any) -> ActionResult:
        answer, evidences = _resolve_answer_and_evidences(payload)
        if not answer:
            return ActionResult(ok=False, error="answer 가 비어있음")
        if not isinstance(evidences, list) or not evidences:
            return ActionResult(ok=False, error="evidences 가 비어있음")

        # Gemma 중심 설계 — 기본 llm. 로컬 토큰 매칭은 --set mode=local 로 명시.
        mode = str(payload.get("mode") or "llm")
        kwargs: dict[str, Any] = {"mode": mode}
        for opt in ("min_claim_score", "max_claims", "high", "low", "timeout_s"):
            if payload.get(opt) is not None:
                try:
                    kwargs[opt] = float(payload[opt]) if opt in {"high", "low", "min_claim_score"} else int(payload[opt])
                except (TypeError, ValueError):
                    pass
        if str(payload.get("deep")).lower() == "true":
            kwargs["deep"] = True

        report = detect(answer, evidences, **kwargs)
        # 전체 verdict 가 supported/partial 이면 ok=True, unsupported 면 ok=False
        # (loop.verify 가 ok 를 verify 기준으로 쓸 수 있게.)
        ok = report.overall in {"supported", "partial", "no_claims"}
        return ActionResult(
            ok=ok,
            output=report.to_dict(),
            meta={"overall": report.overall, "score": round(report.score, 3)},
        )


@register("cross_check")
def _factory() -> CrossCheckAction:
    return CrossCheckAction()
