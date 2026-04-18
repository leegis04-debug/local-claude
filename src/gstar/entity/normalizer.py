"""한국어 형태소 기반 토큰 정규화.

`~/.local/bin/jw` 의 `entity_extract.py` 가 정규식+조사 31개 하드코딩으로 처리하던
조사 변형 (다겸이/다겸의/다겸에) 을 kiwipiepy 형태소 분석으로 대체한다.

- 체언 (NNP/NNG/NNB/SL/SN/SH) 만 반환
- 사용자 사전 지원 (`user_dict_path()`)
- Kiwi 인스턴스는 모듈 레벨 캐시 (최초 로딩 ~500ms)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


_KIWI = None
_KIWI_LOCK = Lock()

# 체언 + 외국어·한자·숫자. 동사·어미·조사·부사는 제외.
_CONTENT_TAGS: frozenset[str] = frozenset(
    {
        "NNG",  # 일반명사
        "NNP",  # 고유명사
        "NNB",  # 의존명사
        "SL",   # 외국어
        "SH",   # 한자
        "SN",   # 숫자
    }
)


@dataclass
class NormalizedToken:
    surface: str
    lemma: str
    pos: str
    start: int
    end: int


def user_dict_path() -> Path:
    """프로젝트별 사용자 사전 위치.

    환경변수 `G_ENTITY_USER_DICT` 우선, 없으면 `~/.gstar/entity_user_dict.tsv`.
    파일 포맷: `단어\tNNP\t0.0` (kiwipiepy 표준).
    """
    p = os.environ.get("G_ENTITY_USER_DICT")
    if p:
        return Path(p)
    return Path.home() / ".gstar" / "entity_user_dict.tsv"


def _get_kiwi():
    global _KIWI
    if _KIWI is not None:
        return _KIWI
    with _KIWI_LOCK:
        if _KIWI is not None:
            return _KIWI
        from kiwipiepy import Kiwi

        k = Kiwi()
        ud = user_dict_path()
        if ud.exists():
            try:
                k.load_user_dictionary(str(ud))
            except Exception:
                pass
        _KIWI = k
        return _KIWI


def normalize(text: str) -> list[NormalizedToken]:
    """텍스트 → 체언 토큰 리스트.

    NNP/NNG/NNB/SL/SH/SN 만. 조사·어미·동사·부사는 제외.
    """
    if not text or not text.strip():
        return []
    kiwi = _get_kiwi()
    out: list[NormalizedToken] = []
    for token in kiwi.tokenize(text):
        if token.tag not in _CONTENT_TAGS:
            continue
        surface = text[token.start : token.start + token.len]
        out.append(
            NormalizedToken(
                surface=surface,
                lemma=token.form,
                pos=token.tag,
                start=token.start,
                end=token.start + token.len,
            )
        )
    return out


def normalize_join_adjacent(tokens: list[NormalizedToken]) -> list[NormalizedToken]:
    """연속 체언을 복합어로 병합.

    kiwipiepy 가 '데이터셋' 을 '데이터'+'셋' 으로 가끔 분할. 연속 NNG/NNP/SL 을 붙여
    복합명사로 재구성한다. start/end 는 확장, lemma 는 surface concat.
    """
    if not tokens:
        return []
    merged: list[NormalizedToken] = []
    cur = tokens[0]
    for nxt in tokens[1:]:
        adjacent = nxt.start == cur.end
        same_class = nxt.pos in {"NNG", "NNP", "SL", "SH"} and cur.pos in {"NNG", "NNP", "SL", "SH"}
        if adjacent and same_class:
            cur = NormalizedToken(
                surface=cur.surface + nxt.surface,
                lemma=cur.lemma + nxt.lemma,
                pos="NNP" if "NNP" in (cur.pos, nxt.pos) else cur.pos,
                start=cur.start,
                end=nxt.end,
            )
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return merged


def extract_content_terms(text: str, *, join_adjacent: bool = True) -> list[str]:
    """편의 함수: 체언 lemma 리스트만 반환."""
    toks = normalize(text)
    if join_adjacent:
        toks = normalize_join_adjacent(toks)
    return [t.lemma for t in toks]
