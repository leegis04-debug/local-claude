from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from local_claude.perf import logger as perf_logger
from local_claude.perf.schema import PerfEvent


def test_log_appends_jsonl(tmp_path: Path) -> None:
    perf_logger.log(action="jw idea", duration_ms=123, exit_code=0, project_dir=tmp_path)
    perf_logger.log(action="jw structure", duration_ms=456, exit_code=1, project_dir=tmp_path)

    path = perf_logger.log_path(tmp_path)
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["action"] == "jw idea"
    assert first["duration_ms"] == 123
    assert first["exit_code"] == 0
    assert first["ts"]

    second = json.loads(lines[1])
    assert second["exit_code"] == 1


def test_log_path_per_day(tmp_path: Path) -> None:
    day1 = datetime(2026, 4, 17, 10, 0, 0)
    day2 = datetime(2026, 4, 18, 10, 0, 0)
    p1 = perf_logger.log_path(tmp_path, when=day1)
    p2 = perf_logger.log_path(tmp_path, when=day2)
    assert p1.name == "log-2026-04-17.jsonl"
    assert p2.name == "log-2026-04-18.jsonl"
    assert p1 != p2


def test_log_mkdir_auto(tmp_path: Path) -> None:
    perf_logger.log(action="x", project_dir=tmp_path)
    assert (tmp_path / ".perf").is_dir()


def test_event_defaults() -> None:
    e = PerfEvent(action="a")
    assert e.duration_ms == 0
    assert e.exit_code == 0
    assert e.retries == 0
    assert e.model is None
    assert e.ts  # ISO 타임스탬프 자동
