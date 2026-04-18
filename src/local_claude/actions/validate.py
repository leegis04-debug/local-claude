"""<validate> — gateway /mcp/jw/{tool} 프록시 (MCP jw-validator).

payload:
  {"tool": "validate_step", "args": {...}}

tool 생략 시 "validate_step" 디폴트.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..infra.gateway import Gateway
from .base import ActionResult, register


@dataclass
class ValidateAction:
    name: str = "validate"
    gateway_factory: Any = field(default=Gateway)

    def execute(self, payload: dict[str, Any], *, gateway: Gateway | None = None) -> ActionResult:
        tool = str(payload.get("tool") or "validate_step").strip()
        args = payload.get("args")
        if args is None:
            # 관대하게 — 남은 키를 args 로 승격.
            args = {k: v for k, v in payload.items() if k not in {"tool", "_body"}}
            body_text = payload.get("_body")
            if body_text and "content" not in args:
                args["content"] = body_text

        gw = gateway or self.gateway_factory()
        resp = gw.mcp_jw(tool, args if isinstance(args, dict) else {"args": args})
        return ActionResult(
            ok=resp.ok,
            output=resp.data if resp.ok else None,
            error=resp.error,
            meta={"tool": tool, "via": resp.via, "status": resp.status_code},
        )


@register("validate")
def _factory() -> ValidateAction:
    return ValidateAction()
