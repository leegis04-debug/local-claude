from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from local_claude.code import git_diff


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 바이너리 없음"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(["init", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["config", "commit.gpgsign", "false"], root)


def test_changed_files_detects_unstaged(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    _git(["add", "a.py"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    (tmp_path / "a.py").write_text("def foo():\n    return 2\n")
    report = git_diff.changed_files(tmp_path)
    assert "a.py" in report.unstaged
    assert report.staged == []


def test_changed_files_detects_staged(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(["add", "a.py"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    (tmp_path / "b.py").write_text("y = 2\n")
    _git(["add", "b.py"], tmp_path)
    report = git_diff.changed_files(tmp_path)
    assert "b.py" in report.staged


def test_impact_finds_cross_file_references(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # 초기 커밋: util.py 에 helper 정의, consumer.py 에서 import.
    (tmp_path / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "consumer.py").write_text(
        "from util import helper\n\nprint(helper())\n"
    )
    (tmp_path / "unrelated.py").write_text("x = 42\n")
    _git(["add", "."], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    # util.py 변경.
    (tmp_path / "util.py").write_text("def helper():\n    return 2\n")
    report = git_diff.impact(tmp_path)

    assert "util.py" in report.changed_files
    assert any(s["name"] == "helper" for s in report.changed_symbols)
    # helper 참조가 consumer.py 에서 발견되어야 함.
    assert "helper" in report.references
    ref_files = [r["file"] for r in report.references["helper"]]
    assert "consumer.py" in ref_files
    # 변경 파일(util.py) 자체는 references 에 제외되어야.
    assert "util.py" not in ref_files


def test_impact_empty_when_no_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(["add", "a.py"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)
    report = git_diff.impact(tmp_path)
    assert report.changed_files == []
    assert report.changed_symbols == []
    assert report.references == {}
