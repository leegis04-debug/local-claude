"""<run_test> — 프로젝트 테스트 실행 + 재시도.

XML 사용 예:
  <run_test retries="1"/>
  <run_test cmd="pytest tests/unit" retries="0"/>

payload:
  - cmd: 명시적 명령 (문자열)
  - retries: 실패 시 재시도 횟수 (기본 0)
  - timeout: 초 (기본 600)
  - cwd: 프로젝트 루트 (기본 현재 cwd)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..code import test_runner
from .base import ActionResult, register


@dataclass
class RunTestAction:
    name: str = "run_test"

    def execute(self, payload: dict[str, Any], **_: Any) -> ActionResult:
        cwd = payload.get("cwd") or "."
        cmd = payload.get("cmd") or payload.get("_body")
        if isinstance(cmd, str):
            cmd = cmd.strip() or None
        retries = int(payload.get("retries") or 0)
        timeout = int(payload.get("timeout") or 600)

        result = test_runner.run(
            Path(cwd),
            cmd=cmd,
            retries=retries,
            timeout_s=timeout,
        )
        return ActionResult(
            ok=result.ok,
            output=result.to_dict(),
            error=None if result.ok else f"exit={result.exit_code}",
            meta={
                "detected": result.detected,
                "attempts": result.attempts,
                "duration_ms": result.duration_ms,
            },
        )


@register("run_test")
def _factory() -> RunTestAction:
    return RunTestAction()
