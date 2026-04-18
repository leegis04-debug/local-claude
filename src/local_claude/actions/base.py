"""Action 공통 인터페이스.

각 action 은 `execute(payload, *, gateway=None) -> ActionResult` 를 구현한다.
executor 가 perf/state 자동 기록을 감싸주므로 action 자체는 순수 로직만 담당.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class ActionResult:
    ok: bool
    output: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "meta": self.meta,
        }


@runtime_checkable
class Action(Protocol):
    name: str

    def execute(self, payload: dict[str, Any], **kwargs: Any) -> ActionResult:  # pragma: no cover
        ...


# 네임 → 팩토리 매핑. 각 action 모듈이 import 시 register() 한다.
REGISTRY: dict[str, Callable[[], Action]] = {}


def register(name: str) -> Callable[[Callable[[], Action]], Callable[[], Action]]:
    def decorator(factory: Callable[[], Action]) -> Callable[[], Action]:
        REGISTRY[name] = factory
        return factory

    return decorator
