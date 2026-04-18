from __future__ import annotations

from pathlib import Path

from local_claude.code import fs_index


def test_file_tree_walk_ignores_common_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: ...")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (tmp_path / "README.md").write_text("hi")

    tree = fs_index.file_tree(tmp_path)
    assert tree.via == "walk"
    rels = set(tree.files)
    assert "README.md" in rels
    assert "src/a.py" in rels
    assert not any(r.startswith("node_modules/") for r in rels)
    assert not any(r.startswith(".git/") for r in rels)
    assert not any(r.startswith("__pycache__/") for r in rels)


def test_file_tree_truncates_at_max_files(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x")
    tree = fs_index.file_tree(tmp_path, max_files=3)
    assert len(tree.files) == 3
    assert tree.truncated is True


def test_key_files_detects_common_configs(tmp_path: Path) -> None:
    for name in (
        "CLAUDE.md",
        "README.md",
        "pyproject.toml",
        "package.json",
        "Makefile",
        "Dockerfile",
        "requirements-dev.txt",
        "unrelated.py",
    ):
        (tmp_path / name).write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "CLAUDE.md").write_text("should not be detected")

    found = fs_index.key_files(tmp_path)
    assert "CLAUDE.md" in found
    assert "README.md" in found
    assert "pyproject.toml" in found
    assert "package.json" in found
    assert "Makefile" in found
    assert "Dockerfile" in found
    assert "requirements-dev.txt" in found  # prefix match
    assert "unrelated.py" not in found
    # 깊이 1만 검색하므로 src/CLAUDE.md 는 포함되지 않음.
    assert not any(f.startswith("src/") for f in found)


def test_source_files_filters_by_extension(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.js").write_text("x")
    (tmp_path / "c.md").write_text("x")
    files = fs_index.source_files(tmp_path, extensions=[".py", ".js"])
    names = sorted(f.name for f in files)
    assert names == ["a.py", "b.js"]
