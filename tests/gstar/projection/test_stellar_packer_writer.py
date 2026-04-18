"""Stellar packer + writer 통합 테스트 (stub 렌더)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gstar.projection.renderer import render_section
from gstar.projection.stage_roles import PROPOSAL_ROLES
from gstar.projection.stellar_packer import pack_sections
from gstar.projection.summarizer import Fact, PrevSummary
from gstar.projection.template import PROPOSAL_8
from gstar.projection.writer import write as writer_write
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def test_pack_sections_produces_input_per_spec():
    prev = PrevSummary(
        facts=[Fact(text="x", kind="metric", source_stage="idea", source_hash="h", track="proposal")],
        skeleton="sk",
        citations=["h"],
    )
    sections = pack_sections(PROPOSAL_8, prev, [], [])
    assert len(sections) == len(PROPOSAL_8)
    for s in sections:
        assert len(s.render()) <= s.max_chars


def test_render_section_stub_mode(monkeypatch, store):
    monkeypatch.setenv("GP_RENDER_MODE", "stub")
    prev = PrevSummary(facts=[], skeleton="", citations=[])
    sections = pack_sections(PROPOSAL_8, prev, [], [])
    role = PROPOSAL_ROLES["proposal"]
    out = render_section(sections[0], role, store, "proj1", "proposal", coherence_mode="loose")
    assert out.text
    assert out.attempts >= 1


def test_writer_saves_markdown_and_run(monkeypatch, tmp_path: Path, store):
    monkeypatch.setenv("GP_RENDER_MODE", "stub")
    prev = PrevSummary(facts=[], skeleton="", citations=["c1", "c2"])
    sections = pack_sections(PROPOSAL_8, prev, [], [])
    role = PROPOSAL_ROLES["proposal"]
    rendered = [
        render_section(s, role, store, "proj1", "proposal", coherence_mode="loose")
        for s in sections
    ]
    result = writer_write(
        rendered,
        tmp_path,
        "proposal",
        output_format="markdown",
        citations=["c1", "c2"],
        store=store,
        project_id="proj1",
        track="proposal",
        duration_ms=1000,
        ollama_model="gemma3:4b-it-qat",
    )
    assert result.output_path.exists()
    content = result.output_path.read_text(encoding="utf-8")
    assert "citations: c1, c2" in content
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM projection_run WHERE project_id = ?", ["proj1"]
    ).fetchone()
    assert rows[0] == 1
