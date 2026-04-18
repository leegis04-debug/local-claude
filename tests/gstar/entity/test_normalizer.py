"""Entity normalizer 테스트 — kiwipiepy 기반 한국어 형태소 정규화."""

from __future__ import annotations

import pytest

pytest.importorskip("kiwipiepy")

from gstar.entity.normalizer import (  # noqa: E402
    extract_content_terms,
    normalize,
    normalize_join_adjacent,
)


def test_normalize_empty_text_returns_empty():
    assert normalize("") == []
    assert normalize("   ") == []


def test_normalize_keeps_content_pos_only():
    toks = normalize("다겸이는 프로젝트에서 일한다")
    pos_tags = {t.pos for t in toks}
    assert pos_tags.issubset({"NNG", "NNP", "NNB", "SL", "SH", "SN"})
    assert "JX" not in pos_tags
    assert "VV" not in pos_tags
    assert "JKB" not in pos_tags


def test_normalize_strips_postpositions_via_morphology():
    """조사 변형이 다른 문장을 넣어도 같은 lemma 가 나와야 한다.

    표준 사전 등재 명사("프로젝트") 기준으로 조사 변형 (는/이/을/의/과/에게) 제거 검증.
    미등록 고유명사는 user_dict 로 등록해야 한다 (test_user_dict_registers_proper_noun 참조).
    """
    variants = [
        "프로젝트는 시작됐다",
        "프로젝트가 시작됐다",
        "프로젝트를 수행한다",
        "프로젝트의 목표",
        "프로젝트와 함께",
        "프로젝트에서 논의",
        "프로젝트에게 전달",
    ]
    lemmas_per_sentence = [set(extract_content_terms(v)) for v in variants]
    for i, lemmas in enumerate(lemmas_per_sentence):
        assert "프로젝트" in lemmas, f"variant #{i} lemmas={lemmas}"


def test_user_dict_registers_proper_noun(tmp_path, monkeypatch):
    """user_dict.tsv 등록으로 미등록 고유명사도 단일 NNP 로 분석."""
    import importlib

    from gstar.entity import normalizer as nm

    ud = tmp_path / "user_dict.tsv"
    ud.write_text("다겸\tNNP\t0.0\n", encoding="utf-8")
    monkeypatch.setenv("G_ENTITY_USER_DICT", str(ud))
    # 모듈 캐시 리셋 (Kiwi 새 인스턴스)
    nm._KIWI = None
    importlib.reload(nm)
    monkeypatch.setenv("G_ENTITY_USER_DICT", str(ud))

    lemmas_per = [
        set(nm.extract_content_terms(v))
        for v in [
            "다겸은 왔다",
            "다겸이 왔다",
            "다겸의 프로젝트",
            "다겸에게 전달",
        ]
    ]
    for i, lemmas in enumerate(lemmas_per):
        assert "다겸" in lemmas, f"variant #{i} lemmas={lemmas}"
    nm._KIWI = None  # 다음 테스트 영향 차단


def test_normalize_english_and_acronym_tokens_preserved():
    toks = normalize("AP@0.5 = 85.3% 기준으로 mAP 평가")
    lemmas = [t.lemma for t in toks]
    assert any(l.upper() in {"AP", "MAP"} for l in lemmas)


def test_normalize_join_adjacent_merges_compound_nouns():
    toks = normalize("데이터셋을 평가했다")
    merged = normalize_join_adjacent(toks)
    surfaces = [t.surface for t in merged]
    assert any("데이터" in s and "셋" in s for s in surfaces), surfaces


def test_extract_content_terms_returns_strings():
    terms = extract_content_terms("비전 AI 육묘 품질 판별 프로젝트")
    assert terms, "체언 추출 실패"
    assert all(isinstance(t, str) for t in terms)
    assert any("비전" in t or "육묘" in t or "프로젝트" in t for t in terms)


def test_normalize_preserves_offset_info():
    text = "프로젝트는 중요하다"
    toks = normalize(text)
    for t in toks:
        assert 0 <= t.start < t.end <= len(text)
        assert text[t.start : t.end] == t.surface


def test_idempotent_across_calls():
    """같은 텍스트 두 번 호출 결과 동일 (Kiwi 인스턴스 캐시 검증)."""
    text = "G 시스템은 DuckDB 와 FAISS 를 쓴다"
    r1 = normalize(text)
    r2 = normalize(text)
    assert [(t.lemma, t.pos, t.start, t.end) for t in r1] == [
        (t.lemma, t.pos, t.start, t.end) for t in r2
    ]


def test_multiple_mentions_all_captured():
    text = "다겸이 연구하고 다겸의 기록을 남겼으며 다겸은 발표했다"
    toks = normalize(text)
    lemmas = [t.lemma for t in toks]
    assert lemmas.count("다겸") + sum(1 for l in lemmas if l.startswith("다겸")) >= 2
