"""User 층 — ~/.local-claude/user/preferences.json (자동 mkdir).

Second Brain(connect-ai GitHub 동기화 저장소)은 Phase 2 에서 통합.
여기서는 local-claude 자체 소유 데이터 디렉토리만 다룬다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from . import _store


def _path() -> Path:
    # config.USER_MEMORY_PATH 를 런타임 참조 — 테스트 monkeypatch 가능.
    return config.USER_MEMORY_PATH


def append(text: str, category: str = "preference", **meta: Any) -> bool:
    return _store.append(_path(), text, category, meta)


def all() -> list[dict[str, Any]]:  # noqa: A001
    return _store.entries(_path())
