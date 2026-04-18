"""context_loader 확장 테스트 — 00-input/{BASE,REF,ING} + form-ref.json."""

from __future__ import annotations

import json
from pathlib import Path

from gstar.projection.context_loader import (
    load_form,
    load_input_bundle,
    load_project,
)


def _make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "00-input" / "BASE").mkdir(parents=True)
    (proj / "00-input" / "REF").mkdir()
    (proj / "00-input" / "ING").mkdir()
    (proj / "00-input" / "BASE" / "공고문.md").write_text("# 공고문\n2025 agri-food-ai", encoding="utf-8")
    (proj / "00-input" / "BASE" / "kickoff.md").write_text("킥오프: 책임자 이재원", encoding="utf-8")
    (proj / "00-input" / "REF" / "related.md").write_text("참고 논문 요약", encoding="utf-8")
    (proj / "00-input" / "ING" / "prev-draft.md").write_text("이전 초안", encoding="utf-8")
    return proj


def test_load_input_bundle_reads_all_subdirs(tmp_path: Path):
    proj = _make_project(tmp_path)
    bundle = load_input_bundle(proj)
    base_names = {r.path.name for r in bundle.base}
    assert "공고문.md" in base_names
    assert "kickoff.md" in base_names
    assert any(r.path.name == "related.md" for r in bundle.ref)
    assert any(r.path.name == "prev-draft.md" for r in bundle.ing)


def test_load_input_bundle_empty_when_no_00_input(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    bundle = load_input_bundle(tmp_path / "empty")
    assert bundle.all_results == []


def test_load_input_bundle_as_artifact_contains_texts(tmp_path: Path):
    proj = _make_project(tmp_path)
    bundle = load_input_bundle(proj)
    art = bundle.as_artifact()
    assert art is not None
    assert art.stage == "input"
    assert art.seq == 0
    assert "공고문" in art.text
    assert "이재원" in art.text


def test_load_project_includes_input_as_seq_zero(tmp_path: Path):
    proj = _make_project(tmp_path)
    (proj / "01-idea").mkdir()
    (proj / "01-idea" / "idea-canvas.md").write_text("아이디어 본문", encoding="utf-8")

    arts = load_project(proj)
    assert len(arts) >= 2
    assert arts[0].stage == "input"
    assert arts[0].seq == 0
    assert arts[1].stage == "idea"
    assert arts[1].seq == 1


def test_load_project_exclude_input(tmp_path: Path):
    proj = _make_project(tmp_path)
    (proj / "01-idea").mkdir()
    (proj / "01-idea" / "idea-canvas.md").write_text("아이디어", encoding="utf-8")
    arts = load_project(proj, include_input=False)
    assert all(a.stage != "input" for a in arts)
    assert len(arts) == 1


def test_load_project_picks_canonical_main_file(tmp_path: Path):
    proj = tmp_path / "p"
    (proj / "04-spec").mkdir(parents=True)
    (proj / "04-spec" / "_wip_draft.md").write_text("초안", encoding="utf-8")
    (proj / "04-spec" / "tech-spec.md").write_text("기술명세 본문", encoding="utf-8")
    (proj / "04-spec" / "note.md").write_text("메모", encoding="utf-8")
    arts = load_project(proj, include_input=False)
    assert len(arts) == 1
    assert arts[0].path.name == "tech-spec.md"


def test_load_form_from_00_input_ref_json(tmp_path: Path):
    proj = _make_project(tmp_path)
    (proj / "00-input" / "form-ref.json").write_text(
        json.dumps({"form_id": "2025-agrifood-ai", "matched_at": "2026-04-19"}),
        encoding="utf-8",
    )
    fc = load_form(proj)
    assert fc is not None
    assert fc.form_id == "2025-agrifood-ai"


def test_load_form_falls_back_to_old_00_form(tmp_path: Path):
    proj = tmp_path / "proj"
    (proj / "00-form").mkdir(parents=True)
    (proj / "00-form" / "legacy.json").write_text(
        json.dumps({"form_id": "legacy", "title": "구버전"}),
        encoding="utf-8",
    )
    fc = load_form(proj)
    assert fc is not None
    assert fc.form_id == "legacy"


def test_load_form_returns_none_when_absent(tmp_path: Path):
    proj = tmp_path / "bare"
    proj.mkdir()
    assert load_form(proj) is None


def test_load_project_handles_pdf_and_hwpx_in_input(tmp_path: Path):
    """PDF·HWPX 가 BASE/ 에 있어도 context 에 통합된다."""
    import zipfile

    proj = tmp_path / "p"
    (proj / "00-input" / "BASE").mkdir(parents=True)
    hwpx = proj / "00-input" / "BASE" / "양식.hwpx"
    xml_content = (
        """<?xml version="1.0"?><sec><p><t>양식섹션: 추진배경</t></p></sec>"""
    ).encode("utf-8")
    with zipfile.ZipFile(hwpx, "w") as zf:
        zf.writestr("Contents/section0.xml", xml_content)
    arts = load_project(proj)
    assert len(arts) == 1
    assert "양식섹션" in arts[0].text
