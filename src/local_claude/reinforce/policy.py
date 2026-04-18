"""정책 파일 버저닝 — 프롬프트·rule·임계치를 매 사이클 조정 가능.

구조:
  {LC_DATA_DIR}/policy/
    ├── {name}.md                    # 현재 active 정책
    └── revisions/
        ├── {name}-rev-001.md
        ├── {name}-rev-002.md
        └── ...

set() 은 rev-NNN 백업 후 active 교체, rollback() 은 선택 rev 를 active 로 복원.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from .. import config

_REV_FILE_RE = re.compile(r"^(?P<name>.+)-rev-(?P<num>\d{3,})\.md$")


def _policy_dir() -> Path:
    return config.LC_DATA_DIR / "policy"


def _revisions_dir() -> Path:
    return _policy_dir() / "revisions"


def _active_path(name: str) -> Path:
    return _policy_dir() / f"{name}.md"


def _next_rev_number(name: str) -> int:
    revs_dir = _revisions_dir()
    if not revs_dir.exists():
        return 1
    max_num = 0
    for entry in revs_dir.iterdir():
        if not entry.is_file():
            continue
        match = _REV_FILE_RE.match(entry.name)
        if match and match.group("name") == name:
            max_num = max(max_num, int(match.group("num")))
    return max_num + 1


def _rev_path(name: str, num: int) -> Path:
    return _revisions_dir() / f"{name}-rev-{num:03d}.md"


@dataclass
class PolicyInfo:
    name: str
    active_exists: bool
    revision_count: int
    current_length: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "active_exists": self.active_exists,
            "revision_count": self.revision_count,
            "current_length": self.current_length,
        }


def list_policies() -> list[PolicyInfo]:
    root = _policy_dir()
    if not root.exists():
        return []
    out: list[PolicyInfo] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        name = entry.stem
        text = entry.read_text(encoding="utf-8") if entry.exists() else ""
        revs = list_revisions(name)
        out.append(
            PolicyInfo(
                name=name,
                active_exists=True,
                revision_count=len(revs),
                current_length=len(text),
            )
        )
    return out


def show(name: str) -> str | None:
    path = _active_path(name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def set_policy(name: str, content: str) -> int:
    """새 정책 내용을 active 로 저장. 기존이 있으면 revision 백업.

    반환: 생성된 revision 번호 (기존 없었으면 0, 있었으면 백업 번호).
    """
    if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
        raise ValueError("policy name 은 영숫자/_/- 만 허용")

    policy_dir = _policy_dir()
    policy_dir.mkdir(parents=True, exist_ok=True)

    active = _active_path(name)
    rev_num = 0
    if active.exists():
        rev_num = _next_rev_number(name)
        _revisions_dir().mkdir(parents=True, exist_ok=True)
        _rev_path(name, rev_num).write_text(active.read_text(encoding="utf-8"), encoding="utf-8")

    # atomic
    tmp = active.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(active)
    return rev_num


def list_revisions(name: str) -> list[int]:
    revs_dir = _revisions_dir()
    if not revs_dir.exists():
        return []
    nums: list[int] = []
    for entry in revs_dir.iterdir():
        if not entry.is_file():
            continue
        match = _REV_FILE_RE.match(entry.name)
        if match and match.group("name") == name:
            nums.append(int(match.group("num")))
    return sorted(nums)


def show_revision(name: str, num: int) -> str | None:
    path = _rev_path(name, num)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def diff_revision(name: str, num: int) -> str:
    """rev N 과 현재 active 의 unified diff."""
    active = show(name) or ""
    rev = show_revision(name, num)
    if rev is None:
        return ""
    return "".join(
        unified_diff(
            rev.splitlines(keepends=True),
            active.splitlines(keepends=True),
            fromfile=f"{name}-rev-{num:03d}.md",
            tofile=f"{name}.md",
        )
    )


def rollback(name: str, num: int) -> bool:
    """rev N 을 active 로 복원. 현재 active 는 먼저 새 revision 으로 백업.

    반환: 복원 성공 여부.
    """
    target = show_revision(name, num)
    if target is None:
        return False
    set_policy(name, target)  # 기존 active 자동 백업
    return True


def export_all() -> dict[str, str]:
    """전체 active 정책을 한 번에 덤프 — 백업/공유용."""
    return {p.name: (show(p.name) or "") for p in list_policies()}


def import_from_iter(items: Iterable[tuple[str, str]]) -> list[int]:
    """여러 정책을 일괄 set. 반환은 각 백업 rev 번호 리스트."""
    return [set_policy(name, content) for name, content in items]
