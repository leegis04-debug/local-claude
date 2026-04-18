"""파일 트리 + key files 자동 감지.

- git 레포면 `git ls-files` 로 tracked 파일만 (gitignore 자동 준수).
- 아니면 rglob 에 일반적 무시 디렉토리 제외.
- Plan 명시대로 **200 파일 상한** 적용.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_MAX_FILES = 200

# 이름이 정확히 일치하거나 prefix 매칭되는 "중요" 파일 패턴.
_KEY_FILE_NAMES: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "README.rst",
    "README",
    "ROADMAP.md",
    "CHANGELOG.md",
    "LICENSE",
    "state.json",
    "status.md",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "tsconfig.json",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    ".env.example",
)

_KEY_FILE_PREFIXES: tuple[str, ...] = (
    "requirements",  # requirements-dev.txt 등
)

_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", "__pycache__",
        "node_modules", "dist", "build", ".next",
        ".venv", "venv", "env", ".env",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "target",  # rust
        ".idea", ".vscode",
    }
)


@dataclass
class FileTree:
    files: list[str]  # 프로젝트 루트 상대경로 (정렬됨)
    truncated: bool  # 200 상한 초과로 잘렸는가
    via: str  # "git" | "walk"

    def to_dict(self) -> dict[str, object]:
        return {"files": self.files, "truncated": self.truncated, "via": self.via}


def _git_tracked(project_root: Path) -> list[str] | None:
    if not (project_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _walk_files(project_root: Path) -> list[str]:
    out: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(project_root).parts)
        if parts & _IGNORE_DIRS:
            continue
        out.append(str(path.relative_to(project_root)))
    return out


def file_tree(
    project_root: Path | str = ".",
    *,
    max_files: int = DEFAULT_MAX_FILES,
) -> FileTree:
    root = Path(project_root).resolve()
    tracked = _git_tracked(root)
    # tracked 가 빈 리스트면 "git init 직후" 상태 — walk 로 넘어가는 게 실용적.
    if tracked:
        files = sorted(tracked)
        via = "git"
    else:
        files = sorted(_walk_files(root))
        via = "walk"
    truncated = len(files) > max_files
    return FileTree(files=files[:max_files], truncated=truncated, via=via)


def _is_key_file(rel: str) -> bool:
    name = Path(rel).name
    if name in _KEY_FILE_NAMES:
        return True
    for prefix in _KEY_FILE_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def key_files(
    project_root: Path | str = ".",
    *,
    max_files: int | None = None,
) -> list[str]:
    """프로젝트 루트의 key file 후보를 반환. 하위 디렉토리 미포함(깊이 1)."""
    root = Path(project_root).resolve()
    candidates: list[str] = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.is_file() and _is_key_file(entry.name):
                candidates.append(entry.name)
    except OSError:
        pass
    if max_files is not None:
        candidates = candidates[:max_files]
    return candidates


def source_files(
    project_root: Path | str = ".",
    *,
    extensions: Iterable[str] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
) -> list[Path]:
    """심볼 인덱스 대상 소스 파일 목록 (절대 경로)."""
    root = Path(project_root).resolve()
    tree = file_tree(root, max_files=max_files * 2)  # 필터 전 여유
    ext_set = {e.lower() for e in extensions} if extensions else None
    out: list[Path] = []
    for rel in tree.files:
        if ext_set is not None and Path(rel).suffix.lower() not in ext_set:
            continue
        out.append(root / rel)
        if len(out) >= max_files:
            break
    return out
