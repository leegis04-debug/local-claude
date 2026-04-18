"""개체 후보 추출.

레거시 구현 (하위 호환) + Phase A normalizer 기반 신규 경로.
- `extract_candidates()` — 기존 시그니처 유지 (정규식+조사 stripping)
- `extract_candidates_morph()` — 신규: kiwipiepy 형태소 기반. kiwipiepy 미설치 시 자동
  폴백.
- `pipeline.py` 는 `G_ENTITY_MORPH=off` 환경변수가 아닌 한 신규 경로 사용.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass

from gstar.ingest.chunker import Fact


_KR_CHUNK = re.compile(r"[가-힣]{2,}")
_EN_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9]{1,}\b")
_EN_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")

_KR_POSTPOSITIONS = (
    "들이",
    "에서",
    "으로",
    "에게",
    "까지",
    "부터",
    "조차",
    "마저",
    "이나",
    "이며",
    "이다",
    "이라",
    "에는",
    "에도",
    "으로써",
    "처럼",
    "라는",
    "이라는",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "도",
    "의",
    "과",
    "와",
    "에",
    "만",
    "로",
    "다",
)

_STOPWORDS = {
    "그리고",
    "하지만",
    "그러나",
    "또한",
    "즉",
    "만약",
    "이것",
    "저것",
    "우리",
    "당신",
    "경우",
    "내용",
    "정도",
    "이번",
    "다음",
    "이후",
    "이전",
    "지금",
    "현재",
    "문제",
    "방법",
    "결과",
    "예를",
}


@dataclass
class EntityCandidate:
    name: str              # 정규화된 대표 이름
    mentions: int          # 등장 횟수
    fact_ids: set[str]     # 출현한 fact 노드 id 집합


def extract_candidates(facts: list[tuple[str, Fact]], min_count: int = 2) -> list[EntityCandidate]:
    """facts: list of (fact_node_id, Fact). 반환: 후보 엔티티 리스트."""

    raw_counts: Counter[str] = Counter()
    fact_map: dict[str, set[str]] = {}

    for fact_id, fact in facts:
        names = _extract_from_text(fact.text)
        for n in names:
            raw_counts[n] += 1
            fact_map.setdefault(n, set()).add(fact_id)

    results: list[EntityCandidate] = []
    for name, count in raw_counts.items():
        if count < min_count and not _is_strong_proper(name):
            continue
        results.append(EntityCandidate(name=name, mentions=count, fact_ids=fact_map[name]))

    results.sort(key=lambda e: (-e.mentions, e.name))
    return results


def _extract_from_text(text: str) -> set[str]:
    names: set[str] = set()
    for m in _KR_CHUNK.finditer(text):
        n = _strip_kr_postposition(m.group(0))
        if n and n not in _STOPWORDS and len(n) >= 2:
            names.add(n)
    for m in _EN_PROPER.finditer(text):
        names.add(m.group(0))
    for m in _EN_ACRONYM.finditer(text):
        names.add(m.group(0))
    return names


def _strip_kr_postposition(word: str) -> str:
    for p in _KR_POSTPOSITIONS:
        if len(word) > len(p) and word.endswith(p):
            return word[: -len(p)]
    return word


def _is_strong_proper(name: str) -> bool:
    """빈도가 낮아도 유지할 강한 고유명사 신호."""
    if _EN_ACRONYM.fullmatch(name) and len(name) >= 2:
        return True
    if _EN_PROPER.fullmatch(name) and len(name) >= 3:
        return True
    return False


def extract_candidates_morph(
    facts: list[tuple[str, Fact]], min_count: int = 2
) -> list[EntityCandidate]:
    """Phase A: 형태소 분석 (조사 처리) + 레거시 regex chunk (미등록 고유명사 보존) 유니온.

    - 조사 변형(다겸이/다겸의)은 kiwipiepy 가 동일 lemma 로 정규화
    - 한국어 고유명사(이재원)처럼 kiwipiepy 가 분할하는 경우는 레거시 regex chunk 로 복구
    - `G_ENTITY_MORPH=off` 시 레거시 단독 경로로 폴백
    """
    if os.environ.get("G_ENTITY_MORPH", "on").lower() == "off":
        return extract_candidates(facts, min_count)
    try:
        from gstar.entity.normalizer import normalize, normalize_join_adjacent
    except ImportError:
        return extract_candidates(facts, min_count)

    raw_counts: Counter[str] = Counter()
    fact_map: dict[str, set[str]] = {}

    for fact_id, fact in facts:
        names: set[str] = set()

        toks = normalize(fact.text)
        if toks:
            toks = normalize_join_adjacent(toks)
            for t in toks:
                if t.pos in ("NNP", "NNG") and len(t.lemma) >= 2 and t.lemma not in _STOPWORDS:
                    names.add(t.lemma)

        for m in _KR_CHUNK.finditer(fact.text):
            stripped = _strip_kr_postposition(m.group(0))
            if stripped and stripped not in _STOPWORDS and len(stripped) >= 2:
                names.add(stripped)

        for m in _EN_PROPER.finditer(fact.text):
            names.add(m.group(0))
        for m in _EN_ACRONYM.finditer(fact.text):
            names.add(m.group(0))

        for name in names:
            raw_counts[name] += 1
            fact_map.setdefault(name, set()).add(fact_id)

    results: list[EntityCandidate] = []
    for name, count in raw_counts.items():
        if count < min_count and not _is_strong_proper(name):
            continue
        results.append(EntityCandidate(name=name, mentions=count, fact_ids=fact_map[name]))
    results.sort(key=lambda e: (-e.mentions, e.name))
    return results
