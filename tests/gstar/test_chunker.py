"""chunker 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from gstar.ingest.chunker import chunk_file, chunk_path


def test_chunk_markdown_headers_and_lists(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text(
        "# 제목\n\n"
        "## 배경\n"
        "이재원은 다겸의 책임연구원이다.\n"
        "OptiREC 은 추천 시스템이다.\n\n"
        "## 목표\n"
        "- RAG 성능 개선\n"
        "- 블록체인 기반 무결성 확보\n",
        encoding="utf-8",
    )

    facts = chunk_file(md, root=tmp_path)
    texts = [f.text for f in facts]

    assert "이재원은 다겸의 책임연구원이다." in texts
    assert "OptiREC 은 추천 시스템이다." in texts
    assert "RAG 성능 개선" in texts
    assert "블록체인 기반 무결성 확보" in texts
    # 헤더는 fact 화되지 않음
    assert "제목" not in texts
    # 섹션 메타 보존
    section_for_rag = next(f.section for f in facts if "RAG" in f.text)
    assert section_for_rag == "목표"


def test_chunk_min_length_filter(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text("- OK\n- 아주 긴 문장입니다 의미있음\n", encoding="utf-8")
    facts = chunk_file(md, root=tmp_path)
    texts = [f.text for f in facts]
    assert "OK" not in texts
    assert "아주 긴 문장입니다 의미있음" in texts


def test_chunk_directory(tmp_path: Path):
    (tmp_path / "a.md").write_text("한국어 문장 하나입니다.\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("또 다른 문장 여기 있습니다.\n", encoding="utf-8")
    (tmp_path / "c.pdf").write_text("무시되어야 함", encoding="utf-8")

    facts = chunk_path(tmp_path, root=tmp_path)
    sources = {f.source for f in facts}
    assert "a.md" in sources
    assert "b.txt" in sources
    assert "c.pdf" not in sources
