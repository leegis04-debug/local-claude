"""답변에서 검증할 만한 claim(문장) 을 추출한다.

구두점 분할 + 우선순위 스코어링. NER/형태소 분석 없이 휴리스틱으로만.

스코어 가산:
- 숫자(연도·금액·수치) 포함
- 대문자 고유명사 또는 한글 3자+ 연속어 (모델명·프로젝트명 후보)
- 인용부호·괄호 (출처 표기성 문장)

스코어 감산:
- 10자 미만
- 끝맺음이 `?` 또는 `!`
- "~라고 생각한다", "~인 것 같다" 같은 주관 표현 (근거 검증 대상 아님)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_SPLIT = re.compile(r"(?<=[.?!。！？])\s+|\n+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_UPPER_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9_\-]{2,}\b")
_HANGUL_LONG = re.compile(r"[\uac00-\ud7a3]{3,}")
_QUOTE_OR_PAREN = re.compile(r"[\"'『』「」()\[\]〈〉《》]")
_SUBJECTIVE = re.compile(
    r"(같다|같아|생각한다|느낌|추측|아마|어쩌면|may|might|probably|seems)"
)


@dataclass
class Claim:
    text: str
    index: int
    score: float

    def to_dict(self) -> dict:
        return {"text": self.text, "index": self.index, "score": round(self.score, 3)}


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    pieces = _SENTENCE_SPLIT.split(text.strip())
    return [p.strip() for p in pieces if p and p.strip()]


def _score(sentence: str) -> float:
    if len(sentence) < 10:
        return 0.0
    score = 1.0
    if _NUMBER.search(sentence):
        score += 1.5
    if _UPPER_PROPER.search(sentence):
        score += 1.0
    if _HANGUL_LONG.search(sentence):
        score += 0.5
    if _QUOTE_OR_PAREN.search(sentence):
        score += 0.3
    last = sentence[-1]
    if last in "?!？！":
        score -= 1.0
    if _SUBJECTIVE.search(sentence):
        score -= 0.8
    return max(0.0, score)


def extract_claims(
    answer: str,
    *,
    min_score: float = 1.0,
    max_claims: int | None = None,
) -> list[Claim]:
    """전체 문장을 원 순서로 반환하되, 스코어 < min_score 는 제외.

    `max_claims` 가 주어지면 점수 내림차순 상위 N 만 골라 원 순서로 재배열.
    """
    sentences = split_sentences(answer)
    scored = [Claim(text=s, index=i, score=_score(s)) for i, s in enumerate(sentences)]
    filtered = [c for c in scored if c.score >= min_score]
    if max_claims is not None and len(filtered) > max_claims:
        top = sorted(filtered, key=lambda c: c.score, reverse=True)[:max_claims]
        top_idx = {c.index for c in top}
        filtered = [c for c in filtered if c.index in top_idx]
    return filtered
