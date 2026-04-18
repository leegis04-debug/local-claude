"""<rerank> — 근거 목록을 쿼리 관련도로 재정렬.

XML 사용 예:
  <rerank query="스마트서비스 미트앤퓨쳐" mode="heuristic" top_k="5">
    [{"text": "...", "score": 0.8}, {"text": "..."}]
  </rerank>

payload:
  - query (필수)
  - docs 또는 _body (JSON 배열 또는 객체 {docs: [...]})
  - mode: noop | heuristic(기본) | llm
  - top_k: 정수
  - alpha: heuristic 가중치 (0~1)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..verify import reranker as reranker_mod
from .base import ActionResult, register


def _parse_docs(payload: dict[str, Any]) -> list[reranker_mod.Document]:
    raw = payload.get("docs")
    if raw is None:
        body = payload.get("_body")
        if body:
            try:
                raw = json.loads(body)
            except Exception as exc:
                raise ValueError(f"_body JSON 파싱 실패: {exc}") from exc
    if isinstance(raw, dict) and "docs" in raw:
        raw = raw["docs"]
    if not isinstance(raw, list):
        raise ValueError("docs 가 리스트가 아님 (payload.docs 또는 _body 로 전달)")
    # 문자열 리스트도 허용.
    normalized: list[reranker_mod.Document] = []
    for item in raw:
        if isinstance(item, str):
            normalized.append({"text": item})
        elif isinstance(item, dict):
            normalized.append(item)
        else:
            raise ValueError(f"지원하지 않는 doc 항목: {type(item).__name__}")
    return normalized


@dataclass
class RerankAction:
    name: str = "rerank"

    def execute(self, payload: dict[str, Any], **_: Any) -> ActionResult:
        query = str(payload.get("query") or "").strip()
        if not query:
            return ActionResult(ok=False, error="query 가 비어있음")
        try:
            docs = _parse_docs(payload)
        except ValueError as exc:
            return ActionResult(ok=False, error=str(exc))
        if not docs:
            return ActionResult(ok=True, output=[], meta={"mode": "noop", "count": 0})

        # Gemma 중심 설계 — 기본 llm. 휴리스틱은 --set mode=heuristic 으로 명시.
        mode = str(payload.get("mode") or "llm")
        top_k_raw = payload.get("top_k")
        top_k = int(top_k_raw) if top_k_raw not in (None, "") else None
        kwargs: dict[str, Any] = {}
        if mode == "heuristic" and payload.get("alpha") is not None:
            try:
                kwargs["alpha"] = float(payload["alpha"])
            except (TypeError, ValueError):
                pass

        reranker = reranker_mod.get_reranker(mode, **kwargs)
        ranked = reranker.rerank(query, docs, top_k=top_k)
        return ActionResult(
            ok=True,
            output=[r.to_dict() for r in ranked],
            meta={"mode": reranker.name, "count": len(ranked), "input_count": len(docs)},
        )


@register("rerank")
def _factory() -> RerankAction:
    return RerankAction()
