"""<ask_deep> — 4090 a4b 로 긴 추론 위임.

`~/.local/bin/ask-gemma --deep` 을 subprocess 로 호출한다.
gateway 경유 옵션도 지원 (/skill/draft 보다는 ask-gemma 가 범용).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .. import config
from .base import ActionResult, register


@dataclass
class AskDeepAction:
    name: str = "ask_deep"
    timeout_s: int = 120

    def execute(self, payload: dict[str, Any], **kwargs: Any) -> ActionResult:
        prompt = str(payload.get("prompt") or payload.get("_body") or "").strip()
        if not prompt:
            return ActionResult(ok=False, error="prompt 가 비어있음")

        bin_path = shutil.which(config.ASK_GEMMA_BIN) or config.ASK_GEMMA_BIN
        cmd = [bin_path, "--deep"]
        if payload.get("rag"):
            cmd.append("--rag")

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=int(payload.get("timeout") or self.timeout_s),
                check=False,
            )
        except FileNotFoundError:
            return ActionResult(ok=False, error=f"ask-gemma 를 찾을 수 없음: {bin_path}")
        except subprocess.TimeoutExpired:
            return ActionResult(ok=False, error="ask-gemma 타임아웃")

        if result.returncode != 0:
            return ActionResult(
                ok=False,
                error=f"ask-gemma exit={result.returncode}",
                output=result.stderr.strip()[-500:],
            )
        return ActionResult(
            ok=True,
            output=result.stdout.strip(),
            meta={"model": "gemma4:a4b", "rag": bool(payload.get("rag"))},
        )


@register("ask_deep")
def _factory() -> AskDeepAction:
    return AskDeepAction()
