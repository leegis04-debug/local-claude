"""한/영 혼용 토큰화 — zero-dep 공통 유틸.

정교한 형태소 분석 대신 `\\w+` 추출 + 한글 char bi-gram 보조.
완벽하진 않지만 Jaccard/containment 매칭에는 충분히 안정적이다.
"""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]+")
# 불용어는 최소 — 과도한 필터는 claim 매칭에서 오히려 해롭다.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "은", "는", "이", "가", "을", "를", "의", "에", "와", "과",
        "도", "만", "로", "으로", "까지", "부터", "이다", "있다", "없다",
        "the", "a", "an", "of", "to", "in", "for", "on", "is", "are",
        "and", "or", "that", "this", "by", "with",
    }
)


def _hangul_bigrams(chunk: str) -> list[str]:
    if len(chunk) < 2:
        return [chunk]
    return [chunk[i : i + 2] for i in range(len(chunk) - 1)]


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def tokens(text: str, *, include_bigrams: bool = True) -> list[str]:
    """단어 토큰 + 한글 bi-gram (선택). 중복 포함 (카운트 반영 가능)."""
    out: list[str] = []
    for chunk in _WORD_RE.findall(normalize(text)):
        if chunk in _STOPWORDS:
            continue
        if chunk.isascii():
            out.append(chunk)
        else:
            # 한글 덩어리 — 단어 + char bigrams 둘 다 기여.
            out.append(chunk)
            if include_bigrams:
                out.extend(_hangul_bigrams(chunk))
    return out


def token_set(text: str, *, include_bigrams: bool = True) -> set[str]:
    return set(tokens(text, include_bigrams=include_bigrams))


def containment(a: str, b: str) -> float:
    """a 의 토큰 중 몇 % 가 b 에 포함되는가 (a → b 방향)."""
    ta = token_set(a)
    if not ta:
        return 0.0
    tb = token_set(b)
    return len(ta & tb) / len(ta)


def jaccard(a: str, b: str) -> float:
    ta = token_set(a)
    tb = token_set(b)
    if not ta and not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
