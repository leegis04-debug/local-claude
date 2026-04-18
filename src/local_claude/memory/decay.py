"""메모리 decay — 카테고리별 TTL + touch_count 보호 (Phase 5 확장).

Plan §② 원안은 "30일 decay" 단일 규칙. Phase 5 에서:
- decision=90d / failure=30d / fact=30d / preference=∞ (카테고리별 차등)
- touch_count >= DEFAULT_TOUCH_PROTECTION 이면 decay 제외 (자주 쓰이는 항목 보호)

기존 시그니처 호환: `decay(project_dir, days=30)` 처럼 `days` 를 명시하면
모든 카테고리에 동일 적용 (legacy mode) — 기존 테스트 그대로 통과.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import config
from . import _store

DEFAULT_DAYS = 30
DEFAULT_TOUCH_PROTECTION = 3

# 카테고리별 TTL (일). None = 영원 보존.
DEFAULT_TTL_DAYS: dict[str, int | None] = {
    "decision": 90,
    "failure": 30,
    "fact": 30,
    "preference": None,
}


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _archive_path(source: Path) -> Path:
    return source.with_name(source.stem + "-archive.json")


def _ttl_for(
    category: str,
    ttl_map: dict[str, int | None],
    fallback_days: int,
) -> int | None:
    if category in ttl_map:
        return ttl_map[category]
    return fallback_days


def _decay_file(
    path: Path,
    *,
    days: int | None,
    ttl_map: dict[str, int | None],
    touch_protection: int,
) -> int:
    if not path.exists():
        return 0
    data = _store.load(path)
    entries: list[dict[str, Any]] = data.get("entries", [])
    now = datetime.now().astimezone()

    keep: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for entry in entries:
        # touch_count 보호 — 자주 쓰이는 항목은 무조건 keep.
        if int(entry.get("touch_count", 0)) >= touch_protection:
            keep.append(entry)
            continue

        category = str(entry.get("category") or "fact")
        if days is not None:
            ttl_days: int | None = days  # legacy 균일 모드
        else:
            ttl_days = _ttl_for(category, ttl_map, DEFAULT_DAYS)
        if ttl_days is None:  # preference 같은 영원 카테고리
            keep.append(entry)
            continue

        ts_str = entry.get("last_seen") or entry.get("created_at")
        ts = _parse(ts_str) if ts_str else None
        cutoff = now - timedelta(days=ttl_days)
        if ts is not None and ts < cutoff:
            moved.append(entry)
        else:
            keep.append(entry)

    if not moved:
        return 0

    archive_path = _archive_path(path)
    archive_data = _store.load(archive_path) if archive_path.exists() else {"entries": []}
    archive_data["entries"].extend(moved)
    _store.save(archive_path, archive_data)

    data["entries"] = keep
    _store.save(path, data)
    return len(moved)


def decay(
    project_dir: Path | str | None = None,
    days: int | None = None,
    *,
    ttl_map: dict[str, int | None] | None = None,
    touch_protection: int = DEFAULT_TOUCH_PROTECTION,
) -> dict[str, int]:
    """오래된 항목을 archive 로 분리.

    days 를 명시하면 카테고리 무시하고 전부 그 일수 기준 (legacy).
    days=None 이면 `ttl_map` 또는 DEFAULT_TTL_DAYS 카테고리 매핑 사용.
    """
    ttl = ttl_map if ttl_map is not None else DEFAULT_TTL_DAYS
    return {
        "project": _decay_file(
            config.project_memory_path(project_dir),
            days=days,
            ttl_map=ttl,
            touch_protection=touch_protection,
        ),
        "user": _decay_file(
            config.USER_MEMORY_PATH,
            days=days,
            ttl_map=ttl,
            touch_protection=touch_protection,
        ),
    }
