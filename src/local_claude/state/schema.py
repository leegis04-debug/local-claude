"""state.json 스키마 — plan 파일 §④ 상태 압축기."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class State(BaseModel):
    project: str = ""
    current_goal: str = ""
    recent_failures: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    next_action: str = ""
    decisions: list[str] = Field(default_factory=list)
    last_action: str = ""
    last_exit_code: int = 0
    updated_at: str = Field(default_factory=_now_iso)

    def touch(self) -> None:
        self.updated_at = _now_iso()
