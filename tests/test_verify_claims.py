from __future__ import annotations

from local_claude.verify.claims import extract_claims, split_sentences


def test_split_mixed_punctuation() -> None:
    text = "첫 문장. 둘째 문장!\n셋째 문장? 끝."
    assert split_sentences(text) == ["첫 문장.", "둘째 문장!", "셋째 문장?", "끝."]


def test_numbers_boost_priority() -> None:
    answer = "이 문장은 검증 대상이 아니다. 우리 매출은 2024년 1.2억원이었다."
    claims = extract_claims(answer, min_score=0.0)
    idx0 = next(c for c in claims if c.index == 0)
    idx1 = next(c for c in claims if c.index == 1)
    # 숫자 포함 두 번째가 더 높음
    assert idx1.score > idx0.score


def test_short_and_questions_demoted() -> None:
    answer = "짧음. 이 내용은 맞는가? RAG 는 Retrieval Augmented Generation 을 뜻한다."
    claims = extract_claims(answer, min_score=1.0)
    texts = [c.text for c in claims]
    assert "짧음." not in texts
    assert not any(t.endswith("?") for t in texts)


def test_subjective_lowered() -> None:
    answer = "이 프로젝트는 잘 될 것 같다. 데이터베이스는 PostgreSQL 16 을 쓴다."
    claims = extract_claims(answer, min_score=1.0)
    texts = [c.text for c in claims]
    # 주관 표현 문장은 감점 — 객관 fact 가 우선
    assert any("PostgreSQL" in t for t in texts)


def test_max_claims_limits_but_preserves_order() -> None:
    answer = " ".join(f"{i}번 문장은 2026 년 지표이다." for i in range(10))
    claims = extract_claims(answer, max_claims=3)
    assert len(claims) == 3
    # 원 순서 유지
    assert claims[0].index < claims[1].index < claims[2].index


def test_empty_input() -> None:
    assert extract_claims("") == []
    assert extract_claims(None) == []  # type: ignore[arg-type]
