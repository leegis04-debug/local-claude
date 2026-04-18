"""<critique> — gateway /mcp/doc/{tool} 프록시 (MCP doc-thinking).

payload:
  {"tool": "critique_thought", "args": {"thought": "..."}}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..infra.gateway import Gateway
from .base import ActionResult, register


@dataclass
class CritiqueAction:
    name: str = "critique"
    gateway_factory: Any = field(default=Gateway)

    def execute(self, payload: dict[str, Any], *, gateway: Gateway | None = None) -> ActionResult:
        tool = str(payload.get("tool") or "critique_thought").strip()
        args = payload.get("args")
        if args is None:
            args = {k: v for k, v in payload.items() if k not in {"tool", "_body"}}
            body_text = payload.get("_body")
            if body_text and "thought" not in args:
                args["thought"] = body_text

        gw = gateway or self.gateway_factory()
        resp = gw.mcp_doc(tool, args if isinstance(args, dict) else {"args": args})
        return ActionResult(
            ok=resp.ok,
            output=resp.data if resp.ok else None,
            error=resp.error,
            meta={"tool": tool, "via": resp.via, "status": resp.status_code},
        )


@register("critique")
def _factory() -> CritiqueAction:
    return CritiqueAction()
