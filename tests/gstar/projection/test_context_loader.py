"""context_loader 테스트."""

from __future__ import annotations

from pathlib import Path

from gstar.projection.context_loader import (
    load_form,
    load_project,
    prev_stages,
    stage_by_name,
)


def test_load_project_empty_dir(tmp_path: Path):
    assert load_project(tmp_path) == []


def test_load_project_parses_stage_dirs(tmp_path: Path):
    (tmp_path / "01-idea").mkdir()
    (tmp_path / "01-idea" / "idea.md").write_text("아이디어 본문", encoding="utf-8")
    (tmp_path / "02-debate").mkdir()
    (tmp_path / "02-debate" / "debate.md").write_text("찬반 논쟁", encoding="utf-8")
    (tmp_path / "random-folder").mkdir()

    arts = load_project(tmp_path)
    assert len(arts) == 2
    assert arts[0].stage == "idea"
    assert arts[0].seq == 1
    assert arts[1].stage == "debate"


def test_prev_stages_filters_by_seq(tmp_path: Path):
    (tmp_path / "01-idea").mkdir()
    (tmp_path / "01-idea" / "a.md").write_text("1", encoding="utf-8")
    (tmp_path / "02-debate").mkdir()
    (tmp_path / "02-debate" / "a.md").write_text("2", encoding="utf-8")
    (tmp_path / "03-structure").mkdir()
    (tmp_path / "03-structure" / "a.md").write_text("3", encoding="utf-8")

    arts = load_project(tmp_path)
    prev = prev_stages(arts, "structure")
    assert [a.stage for a in prev] == ["idea", "debate"]


def test_stage_by_name_returns_match(tmp_path: Path):
    (tmp_path / "01-idea").mkdir()
    (tmp_path / "01-idea" / "a.md").write_text("x", encoding="utf-8")
    arts = load_project(tmp_path)
    assert stage_by_name(arts, "idea").stage == "idea"
    assert stage_by_name(arts, "nonexistent") is None


def test_load_form_returns_none_if_missing(tmp_path: Path):
    assert load_form(tmp_path) is None


def test_load_form_reads_json(tmp_path: Path):
    fdir = tmp_path / "00-form"
    fdir.mkdir()
    import json

    (fdir / "ref.json").write_text(
        json.dumps({"form_id": "test-form", "title": "T"}),
        encoding="utf-8",
    )
    fc = load_form(tmp_path)
    assert fc is not None
    assert fc.form_id == "test-form"
