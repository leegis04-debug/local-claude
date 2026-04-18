"""표준 JSON envelope 도구 호출 — connect-ai child_process 호환.

envelope:
  {"ok": bool, "name": str, "output": any, "error": str|null,
   "meta": dict, "duration_ms": int}

ok=False 여도 stdout 으로 envelope 반환 (exit code 는 0/1 로 구분).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..actions.base import REGISTRY
from ..orchestrator import executor as orch_executor


def call(
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    project_dir: Path | str | None = None,
    record_perf: bool = True,
    record_state: bool = True,
) -> dict[str, Any]:
    if name not in REGISTRY:
        return {
            "ok": False,
            "name": name,
            "output": None,
            "error": f"unknown tool: {name}",
            "meta": {"available": sorted(REGISTRY.keys())},
            "duration_ms": 0,
        }
    executed = orch_executor.dispatch(
        (name, payload or {}),
        project_dir=project_dir,
        record_perf=record_perf,
        record_state=record_state,
    )
    return {
        "ok": executed.result.ok,
        "name": executed.name,
        "output": executed.result.output,
        "error": executed.result.error,
        "meta": executed.result.meta,
        "duration_ms": executed.duration_ms,
    }
