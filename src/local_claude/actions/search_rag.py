"""<search_rag> — gateway /search/hybrid 프록시.

payload 형식:
  {"query": "스마트서비스", "top_k": 5,
   "exclude_sources": ["govsupport"], "sources": null}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..infra.gateway import Gateway
from .base import ActionResult, register


@dataclass
class SearchRagAction:
    name: str = "search_rag"
    gateway_factory: Any = field(default=Gateway)

    def execute(self, payload: dict[str, Any], *, gateway: Gateway | None = None) -> ActionResult:
        query = str(payload.get("query") or payload.get("_body") or "").strip()
        if not query:
            return ActionResult(ok=False, error="query 가 비어있음")
        gw = gateway or self.gateway_factory()
        top_k = int(payload.get("top_k") or 5)
        exclude = payload.get("exclude_sources")
        if exclude is None:
            exclude = ["govsupport"]
        sources = payload.get("sources")
        resp = gw.search_hybrid(
            query=query,
            top_k=top_k,
            exclude_sources=list(exclude) if exclude else [],
            sources=list(sources) if sources else None,
        )
        return ActionResult(
            ok=resp.ok,
            output=resp.data if resp.ok else None,
            error=resp.error,
            meta={"via": resp.via, "status": resp.status_code, "url": resp.url},
        )


@register("search_rag")
def _factory() -> SearchRagAction:
    return SearchRagAction()
