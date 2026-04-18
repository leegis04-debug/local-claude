"""엔티티 후보 추출 테스트."""

from __future__ import annotations

from gstar.ingest.chunker import Fact
from gstar.ingest.entity_extract import extract_candidates


def _fact(text: str, idx: int) -> tuple[str, Fact]:
    return f"fid{idx}", Fact(text=text, source="t.md", section="", line_no=idx)


def test_extract_frequency_filter():
    facts = [
        _fact("이재원은 다겸의 연구원이다", 1),
        _fact("이재원이 OptiREC 을 만들었다", 2),
        _fact("OptiREC 은 추천 시스템이다", 3),
        _fact("어느 날 한번만 등장하는 단어", 4),
    ]
    cands = extract_candidates(facts, min_count=2)
    names = {c.name for c in cands}

    # 2회 이상 등장하는 한국어 고유명사
    assert "이재원" in names
    # 강한 고유명사(영문 대문자)는 1회여도 유지
    assert "OptiREC" in names
    # 1회만 등장 + 약한 → 제외
    assert "한번만" not in names


def test_extract_acronym_and_proper():
    facts = [
        _fact("RAG 와 LLM 은 밀접하다", 1),
        _fact("Claude 는 LLM 의 한 종류이다", 2),
    ]
    cands = extract_candidates(facts, min_count=1)
    names = {c.name for c in cands}
    assert "RAG" in names
    assert "LLM" in names
    assert "Claude" in names


def test_extract_tracks_fact_ids():
    facts = [
        _fact("RAG 는 검색이다", 1),
        _fact("RAG 는 생성이다", 2),
    ]
    cands = extract_candidates(facts, min_count=1)
    rag = next(c for c in cands if c.name == "RAG")
    assert rag.fact_ids == {"fid1", "fid2"}
    assert rag.mentions == 2


def test_extract_stopword_and_postposition():
    facts = [
        _fact("이재원이 다겸에서 일한다", 1),
        _fact("이재원은 다겸의 연구원", 2),
    ]
    cands = extract_candidates(facts, min_count=2)
    names = {c.name for c in cands}
    # 조사가 제거된 정규화 이름이 수집되어야 함
    assert "이재원" in names
    assert "다겸" in names
