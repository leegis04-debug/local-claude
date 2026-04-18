"""Session 층 — 프로세스 RAM, 프로세스 수명만큼만 산다.

VS Code workspaceState 같은 것은 Phase 2 connect-ai fork 에서 붙인다.
여기서는 동일 Python 인터프리터 안에서 모듈 임포트로 공유되는 단순 dict 로 충분.
"""

from __future__ import annotations

from typing import Any

_STORE: dict[str, Any] = {}


def set(key: str, value: Any) -> None:  # noqa: A001 — session API 일관성
    _STORE[key] = value


def get(key: str, default: Any = None) -> Any:
    return _STORE.get(key, default)


def all() -> dict[str, Any]:  # noqa: A001
    return dict(_STORE)


def clear() -> None:
    _STORE.clear()
