"""{프로젝트}/.perf/log-YYYY-MM-DD.jsonl 에 이벤트를 append."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import project_perf_dir
from .schema import PerfEvent


def log_path(project_dir: Path | str | None = None, when: datetime | None = None) -> Path:
    day = (when or datetime.now()).strftime("%Y-%m-%d")
    return project_perf_dir(project_dir) / f"log-{day}.jsonl"


def log(
    event: PerfEvent | None = None,
    project_dir: Path | str | None = None,
    **kwargs: Any,
) -> PerfEvent:
    if event is None:
        event = PerfEvent(**kwargs)
    path = log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(event.model_dump_json() + "\n")
    return event
