"""성능 이벤트 스키마 — plan 파일 Phase 6 P-Reinforce 입력."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PerfEvent(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now().astimezone().isoformat())
    action: str
    model: str | None = None
    duration_ms: int = 0
    exit_code: int = 0
    validation: str | None = None
    retries: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)
