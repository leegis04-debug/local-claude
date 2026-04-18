"""state.json 읽기·갱신 (atomic write, 손상 복구, 리스트 상한)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import project_state_path
from .schema import State

# 무한 누적 방지 — 오래된 항목부터 잘라낸다.
_LIST_LIMITS: dict[str, int] = {
    "recent_failures": 10,
    "open_issues": 20,
    "decisions": 50,
    "changed_files": 30,
}


def _default_project_name(project_dir: Path | str | None) -> str:
    return (Path(project_dir).expanduser() if project_dir else Path.cwd()).name


def load(project_dir: Path | str | None = None) -> State:
    path = project_state_path(project_dir)
    if not path.exists():
        return State(project=_default_project_name(project_dir))
    try:
        return State.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        # 손상 파일은 옆으로 밀어두고 빈 state 반환 — 사용자가 나중에 검수 가능.
        path.rename(path.with_suffix(path.suffix + ".corrupt"))
        return State(project=_default_project_name(project_dir))


def save(state: State, project_dir: Path | str | None = None) -> None:
    state.touch()
    path = project_state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def update(project_dir: Path | str | None = None, **kwargs: Any) -> State:
    """필드 부분 갱신. 리스트 필드에 스칼라 주면 append + 상한 트림."""
    state = load(project_dir)
    if not state.project:
        state.project = _default_project_name(project_dir)

    for key, value in kwargs.items():
        if value is None or not hasattr(state, key):
            continue
        current = getattr(state, key)
        if isinstance(current, list) and not isinstance(value, list):
            if value in current:
                # dedup — 이미 있으면 맨 뒤로 이동해 최신화.
                current.remove(value)
            current.append(value)
            limit = _LIST_LIMITS.get(key)
            if limit and len(current) > limit:
                del current[: len(current) - limit]
        else:
            setattr(state, key, value)

    save(state, project_dir)
    return state
