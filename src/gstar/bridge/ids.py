"""
ID 네임스페이스 규약 — `project.key` 접두사 기반 동적 생성.

외부 프로젝트마다 키가 다르므로 (DT, AB, SAF 등) 모든 ID 는 cfg.project_key 를 prefix 로.

| 종류 | 패턴 | 예 (key=DT) |
|------|------|-------------|
| Epic | `{K}-E{n}` | DT-E1 |
| Task 일반 | `{K}-T{phase}.{seq}` | DT-T1.1.1 |
| Task 분할 (특허·Tracker 등) | `{K}-T{phase}.{seq}{a/b/c}` | DT-T1.3.3a |
| Paper Task | `{K}-TP{n}.{seq}` | DT-TP1.1 |
| 인프라 Task | `{K}-TInfra.{seq}` | DT-TInfra.1 |
| 연구역량 Task | `{K}-TR.{seq}{a/b/c}` | DT-TR.4a |
| PM Task | `{K}-TPM.{seq}{a/b/c/d}` | DT-TPM.1a |
| 월별 PM Task | `{K}-TPM.{seq}-{yymm}` | DT-TPM.3-jun |
| Sub-task | `{K}-ST{phase}.{seq}{letter}` | DT-ST1.1.1a |
"""

from __future__ import annotations

from gstar.bridge.config import BridgeConfig


def epic(cfg: BridgeConfig, n: int) -> str:
    return f"{cfg.project_key}-E{n}"


def task(cfg: BridgeConfig, phase: str, seq: int | str, suffix: str = "") -> str:
    return f"{cfg.project_key}-T{phase}.{seq}{suffix}"


def paper_task(cfg: BridgeConfig, paper_n: int, seq: int) -> str:
    return f"{cfg.project_key}-TP{paper_n}.{seq}"


def infra_task(cfg: BridgeConfig, seq: int) -> str:
    return f"{cfg.project_key}-TInfra.{seq}"


def research_task(cfg: BridgeConfig, seq: int | str, suffix: str = "") -> str:
    return f"{cfg.project_key}-TR.{seq}{suffix}"


def pm_task(cfg: BridgeConfig, seq: int | str, suffix: str = "") -> str:
    return f"{cfg.project_key}-TPM.{seq}{suffix}"


def pm_task_monthly(cfg: BridgeConfig, seq: int | str, month_tag: str) -> str:
    """month_tag: 'may', 'jun', 'q2', ... (소문자, 숫자 없음)"""
    return f"{cfg.project_key}-TPM.{seq}-{month_tag}"


def subtask(cfg: BridgeConfig, phase: str, seq: int | str, letter: str) -> str:
    """letter: 'a', 'b', 'c', ..."""
    return f"{cfg.project_key}-ST{phase}.{seq}{letter}"


__all__ = [
    "epic", "task", "paper_task", "infra_task",
    "research_task", "pm_task", "pm_task_monthly", "subtask",
]
