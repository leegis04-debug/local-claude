"""<search_graph> — Neo4j cypher read-only 쿼리 (gateway 경유).

payload:
  {"cypher": "MATCH (n:Method) RETURN n LIMIT 5",
   "params": {"limit": 5}}

파괴적 쿼리 차단은 gateway 쪽에 있으나 여기서도 1차 가드.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..infra.gateway import Gateway
from .base import ActionResult, register

_DESTRUCTIVE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


@dataclass
class SearchGraphAction:
    name: str = "search_graph"
    gateway_factory: Any = field(default=Gateway)

    def execute(self, payload: dict[str, Any], *, gateway: Gateway | None = None) -> ActionResult:
        cypher = str(payload.get("cypher") or payload.get("_body") or "").strip()
        if not cypher:
            return ActionResult(ok=False, error="cypher 가 비어있음")
        if _DESTRUCTIVE.search(cypher):
            return ActionResult(
                ok=False,
                error="파괴적 cypher 는 search_graph 에서 금지 (읽기 전용)",
            )
        gw = gateway or self.gateway_factory()
        params = payload.get("params") or {}
        resp = gw.graph_query(cypher=cypher, params=params if isinstance(params, dict) else {})
        return ActionResult(
            ok=resp.ok,
            output=resp.data if resp.ok else None,
            error=resp.error,
            meta={"via": resp.via, "status": resp.status_code},
        )


@register("search_graph")
def _factory() -> SearchGraphAction:
    return SearchGraphAction()
