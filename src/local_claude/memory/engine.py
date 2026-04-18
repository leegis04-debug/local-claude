"""3층 메모리 통합 API (plan 파일 §② 메모리 엔진)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import project as project_layer
from . import session as session_layer
from . import user as user_layer


def remember(
    text: str,
    scope: str = "project",
    category: str = "fact",
    project_dir: Path | str | None = None,
    **meta: Any,
) -> bool:
    """단일 항목을 명시적으로 기록한다. 스코프: session | project | user."""
    if scope == "session":
        session_layer.set(text, {"category": category, **meta})
        return True
    if scope == "user":
        return user_layer.append(text, category=category, **meta)
    return project_layer.append(text, category=category, project_dir=project_dir, **meta)


def recall(project_dir: Path | str | None = None) -> dict[str, Any]:
    """세 층 전부 덤프 — 시스템 프롬프트 주입용."""
    return {
        "session": session_layer.all(),
        "project": project_layer.all(project_dir),
        "user": user_layer.all(),
    }
