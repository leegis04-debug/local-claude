"""Project 층 — {cwd}/.memory/project.json (자동 mkdir)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from . import _store


def _path(project_dir: Path | str | None) -> Path:
    return config.project_memory_path(project_dir)


def append(
    text: str,
    category: str = "fact",
    project_dir: Path | str | None = None,
    **meta: Any,
) -> bool:
    return _store.append(_path(project_dir), text, category, meta)


def all(project_dir: Path | str | None = None) -> list[dict[str, Any]]:  # noqa: A001
    return _store.entries(_path(project_dir))
