"""Action dispatcher — REGISTRY 조회 + perf/state 자동 기록.

Phase 1 의 perf.logger / state.compressor 와 유기적으로 연동된다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..actions.base import REGISTRY, ActionResult
from ..perf import logger as perf_logger
from ..perf.schema import PerfEvent
from ..state import compressor as state_mod
from .parser import ParsedAction


@dataclass
class ExecutedAction:
    name: str
    result: ActionResult
    duration_ms: int


def dispatch(
    action: ParsedAction | dict[str, Any] | tuple[str, dict[str, Any]],
    *,
    project_dir: Path | str | None = None,
    gateway: Any = None,
    record_perf: bool = True,
    record_state: bool = True,
) -> ExecutedAction:
    """단일 action 실행. perf/state 훅 포함."""
    if isinstance(action, ParsedAction):
        name = action.name
        payload = dict(action.payload)
    elif isinstance(action, tuple):
        name, payload = action[0], dict(action[1] or {})
    elif isinstance(action, dict):
        name = str(action.get("name", ""))
        payload = dict(action.get("payload") or {})
    else:
        raise TypeError(f"unsupported action type: {type(action)!r}")

    factory = REGISTRY.get(name)
    if factory is None:
        result = ActionResult(ok=False, error=f"unknown action: {name}")
        duration_ms = 0
    else:
        handler = factory()
        start = time.monotonic()
        try:
            if gateway is not None:
                result = handler.execute(payload, gateway=gateway)
            else:
                result = handler.execute(payload)
        except Exception as exc:  # noqa: BLE001 — 어떤 예외든 perf 로그는 남겨야 함
            result = ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        duration_ms = int((time.monotonic() - start) * 1000)

    if record_perf:
        try:
            perf_logger.log(
                PerfEvent(
                    action=name,
                    duration_ms=duration_ms,
                    exit_code=0 if result.ok else 1,
                    validation=("pass" if result.ok else "fail"),
                ),
                project_dir=project_dir,
            )
        except Exception:
            pass

    if record_state:
        try:
            state_mod.update(
                project_dir,
                last_action=name,
                last_exit_code=0 if result.ok else 1,
            )
        except Exception:
            pass

    return ExecutedAction(name=name, result=result, duration_ms=duration_ms)


def dispatch_all(
    actions: list[ParsedAction],
    *,
    project_dir: Path | str | None = None,
    gateway: Any = None,
    stop_on_failure: bool = False,
) -> list[ExecutedAction]:
    executed: list[ExecutedAction] = []
    for action in actions:
        ex = dispatch(action, project_dir=project_dir, gateway=gateway)
        executed.append(ex)
        if stop_on_failure and not ex.result.ok:
            break
    return executed
