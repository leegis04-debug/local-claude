"""git diff 추적 + 심볼 기반 영향도 역추적.

- `changed_files(base)` : HEAD 와 base 사이 변경 파일 + 스테이지+워킹트리 변경.
- `impact(file)` : 변경 파일의 심볼 이름을 심볼 인덱스에서 뽑아, 그 이름을 참조하는
  다른 파일을 grep 해서 반환. 완벽한 call graph 가 아닌 1-hop 역참조.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import fs_index, symbols as symbols_mod


@dataclass
class DiffReport:
    base: str | None
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    base_to_head: list[str] = field(default_factory=list)

    @property
    def all_changed(self) -> list[str]:
        return sorted(set(self.staged) | set(self.unstaged) | set(self.base_to_head))

    def to_dict(self) -> dict[str, object]:
        return {
            "base": self.base,
            "staged": self.staged,
            "unstaged": self.unstaged,
            "base_to_head": self.base_to_head,
            "all_changed": self.all_changed,
        }


@dataclass
class ImpactReport:
    changed_files: list[str] = field(default_factory=list)
    changed_symbols: list[dict] = field(default_factory=list)
    references: dict[str, list[dict]] = field(default_factory=dict)
    # references["name"] = [{"file": str, "line": int}, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": self.changed_files,
            "changed_symbols": self.changed_symbols,
            "references": self.references,
        }


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def changed_files(
    project_root: Path | str = ".",
    base: str | None = None,
) -> DiffReport:
    root = Path(project_root).resolve()
    report = DiffReport(base=base)

    staged = _git(["diff", "--cached", "--name-only"], root)
    if staged:
        report.staged = [l.strip() for l in staged.splitlines() if l.strip()]

    unstaged = _git(["diff", "--name-only"], root)
    if unstaged:
        report.unstaged = [l.strip() for l in unstaged.splitlines() if l.strip()]

    if base:
        # "<base>..HEAD" 범위. base 가 현존 ref 인지 가드.
        ref_check = _git(["rev-parse", "--verify", base], root)
        if ref_check:
            combined = _git(["diff", "--name-only", f"{base}..HEAD"], root)
            if combined:
                report.base_to_head = [l.strip() for l in combined.splitlines() if l.strip()]

    return report


def _reference_lines(
    project_root: Path,
    name: str,
    *,
    exclude_files: set[str],
    max_files: int,
) -> list[dict]:
    """`name` 을 포함하는 파일들을 grep 으로 수집. 변경 파일 자체는 제외."""
    # \bname\b 경계 — 짧은 이름이 서브스트링 매칭되는 것 방지.
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    references: list[dict] = []
    tree = fs_index.file_tree(project_root, max_files=max_files * 4)  # 스캔 풀 여유
    for rel in tree.files:
        if rel in exclude_files:
            continue
        path = project_root / rel
        try:
            # 텍스트 아닌 파일은 가볍게 skip — 확장자 기반.
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".pdf"}:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as fp:
                for i, line in enumerate(fp, start=1):
                    if pattern.search(line):
                        references.append({"file": rel, "line": i})
                        break  # 파일당 첫 매치만 — 영향도 파악엔 충분
        except OSError:
            continue
        if len(references) >= max_files:
            break
    return references


def impact(
    project_root: Path | str = ".",
    *,
    base: str | None = None,
    max_references_per_symbol: int = 20,
) -> ImpactReport:
    root = Path(project_root).resolve()
    diff = changed_files(root, base=base)
    changed = diff.all_changed
    report = ImpactReport(changed_files=changed)

    if not changed:
        return report

    # 변경 파일에서 심볼 인덱스 구축.
    changed_paths = [root / f for f in changed if (root / f).is_file()]
    sym_index = symbols_mod.build_index(changed_paths, project_root=root)
    report.changed_symbols = sym_index.to_list()

    # 심볼 이름별로 역참조 수집 (중복 이름 dedup).
    exclude = set(changed)
    for name in sym_index.names():
        # 너무 짧은 이름(1~2자)은 노이즈가 심해 건너뜀.
        if len(name) < 3:
            continue
        refs = _reference_lines(
            root, name, exclude_files=exclude, max_files=max_references_per_symbol
        )
        if refs:
            report.references[name] = refs

    return report
