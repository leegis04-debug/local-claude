"""archive 파일에서 항목을 활성 메모리로 복원.

조건: text 부분 매칭 또는 category 일치.
활성 파일에 이미 같은 (text, category) 가 있으면 스킵 (중복 방지).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from . import _store


@dataclass
class RestoreResult:
    restored_project: int = 0
    restored_user: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "project": self.restored_project,
            "user": self.restored_user,
            "skipped": self.skipped,
        }


def _archive_path(source: Path) -> Path:
    return source.with_name(source.stem + "-archive.json")


def _match(entry: dict[str, Any], *, text: str | None, category: str | None) -> bool:
    if text and text not in str(entry.get("text", "")):
        return False
    if category and str(entry.get("category", "")) != category:
        return False
    return True


def _has_duplicate(active_entries: list[dict[str, Any]], entry: dict[str, Any]) -> bool:
    for a in active_entries:
        if a.get("text") == entry.get("text") and a.get("category") == entry.get("category"):
            return True
    return False


def _restore_file(
    active_path: Path, *, text: str | None, category: str | None
) -> tuple[int, int]:
    archive_path = _archive_path(active_path)
    if not archive_path.exists():
        return 0, 0
    archive_data = _store.load(archive_path)
    archive_entries: list[dict[str, Any]] = archive_data.get("entries", [])
    if not archive_entries:
        return 0, 0

    active_data = _store.load(active_path)
    active_entries: list[dict[str, Any]] = active_data.get("entries", [])

    restored: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    skipped = 0
    for entry in archive_entries:
        if not _match(entry, text=text, category=category):
            remaining.append(entry)
            continue
        if _has_duplicate(active_entries, entry):
            skipped += 1
            remaining.append(entry)  # 스킵한 항목은 archive 에 남겨둠
            continue
        restored.append(entry)

    if not restored:
        return 0, skipped

    active_entries.extend(restored)
    active_data["entries"] = active_entries
    _store.save(active_path, active_data)

    archive_data["entries"] = remaining
    _store.save(archive_path, archive_data)
    return len(restored), skipped


def restore(
    project_dir: Path | str | None = None,
    *,
    text: str | None = None,
    category: str | None = None,
) -> RestoreResult:
    if not text and not category:
        # 안전장치 — 조건 없이 전체 복원은 방지.
        return RestoreResult()
    result = RestoreResult()
    p_restored, p_skipped = _restore_file(
        config.project_memory_path(project_dir), text=text, category=category
    )
    u_restored, u_skipped = _restore_file(
        config.USER_MEMORY_PATH, text=text, category=category
    )
    result.restored_project = p_restored
    result.restored_user = u_restored
    result.skipped = p_skipped + u_skipped
    return result
