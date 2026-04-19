"""Phase P-Q2.5 — 질문 중요도 랭킹.

각 QuestionNode 에 대해 0.0 이상의 `score` 를 계산. 높을수록 중요.

스코어 구성 (합산):
  1. 타입 가중치 — heading > row_label > header > answer_slot
  2. Heading depth — 얕을수록 ↑ (depth=1 은 섹션 루트, 흔히 메타라 약간 감점)
  3. Stage 별 키워드 매칭 — "목표", "KPI", "예산" 등
  4. Critical marker — "※", "★", "필수", "반드시"
  5. 분량 스윗스팟 — 5~80자 사이
  6. 금지/희석 요소 — "기타", "(선택)", 너무 긴 지시문 등 약간 감점
  7. [옵션] G 관련성 — /search/fused 로 이 질문에 대응되는 fact 가 얼마나 있는지

`score_question` 만 쓰면 Ollama/G 접근 불필요 → 빠름.
`rank_with_rag` 는 질문당 1 RPC → 전체 N 개면 N 번 왕복.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------- 가중치 상수 -----------------------------------------------------


_TYPE_WEIGHT = {
    "heading": 3.0,
    "table_row_label": 2.5,
    "table_header": 1.5,
    "table": 0.0,                       # 표 자체 노드는 답변 대상 아님
    "table_cell_answer_slot": 1.0,
}


_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "idea": (
        "목표", "비전", "핵심", "차별", "기회", "가치", "필요성",
        "문제", "혁신", "의의", "파급",
    ),
    "debate": (
        "찬", "반", "쟁점", "우려", "대안", "가정",
    ),
    "structure": (
        "구조", "구성", "단계", "체계", "흐름", "프로세스",
        "아키텍처", "모듈", "컴포넌트",
    ),
    "spec": (
        "기술", "TRL", "알고리즘", "모델", "성능", "정확도",
        "지표", "벤치마크", "데이터", "인프라", "API",
    ),
    "risk-check": (
        "리스크", "위험", "대응", "차선", "실패",
    ),
    "experiment-plan": (
        "실험", "검증", "가설", "표본", "통계", "측정",
        "대조군", "KPI", "지표",
    ),
    "proposal": (
        "사업", "목표", "KPI", "예산", "일정", "인력",
        "컨소시엄", "성과", "기대효과", "매출", "수익",
        "TRL", "차별화",
    ),
    "final-doc": (
        "요약", "결론", "핵심", "성과", "종합", "완결",
    ),
    "lab-note": (
        "실험", "결과", "관찰", "데이터", "해석", "차트",
    ),
}


_CRITICAL_PATTERNS = (
    re.compile(r"[※★]"),
    re.compile(r"필\s*수"),
    re.compile(r"반\s*드\s*시"),
    re.compile(r"주의\s*사\s*항"),
    re.compile(r"중\s*요\s*도"),
    re.compile(r"제\s*출"),
)


_DILUTION_PATTERNS = (
    re.compile(r"^\s*기타\s*$"),
    re.compile(r"^\s*\(\s*선택\s*\)"),
    re.compile(r"해당\s*없음"),
    re.compile(r"참\s*고"),
)


# ---------- 코어 ------------------------------------------------------------


@dataclass
class RankedQuestion:
    q_id: str
    seq: int
    title: str
    type: str
    score: float
    breakdown: dict = field(default_factory=dict)


def score_question(q, stage: str) -> RankedQuestion:
    """단일 질문의 heuristic 점수."""
    score = 0.0
    bd: dict = {}

    # 1. 타입 가중치
    w_type = _TYPE_WEIGHT.get(q.type, 0.5)
    score += w_type
    bd["type"] = w_type

    # 2. heading depth
    if q.type == "heading":
        # depth 1 은 문서 최상위 = 파일명/메타 가능성 높음 → 약간 감점
        if q.depth == 1:
            score -= 1.0
            bd["depth_meta_penalty"] = -1.0
        else:
            # depth 2~6 에서 얕을수록 가산 (2 → +4, 3 → +3, ..., 6 → 0)
            boost = max(0.0, 6.0 - q.depth)
            score += boost
            bd["depth_boost"] = boost

    # 3. stage 키워드 매칭
    kws = _STAGE_KEYWORDS.get(stage, ())
    hits = sum(1 for kw in kws if kw in q.title)
    if hits:
        boost = min(hits * 1.5, 6.0)        # 과매칭 방지 상한
        score += boost
        bd["stage_keywords"] = boost

    # 4. critical marker
    for pat in _CRITICAL_PATTERNS:
        if pat.search(q.title):
            score += 5.0
            bd["critical_marker"] = 5.0
            break

    # 5. 분량 스윗스팟
    n = len(q.title.strip())
    if 5 <= n <= 80:
        score += 2.0
        bd["length_sweet"] = 2.0
    elif n > 120:
        score -= 1.0
        bd["length_too_long"] = -1.0
    elif n < 3:
        score -= 2.0
        bd["length_too_short"] = -2.0

    # 6. 희석 요소
    for pat in _DILUTION_PATTERNS:
        if pat.search(q.title):
            score -= 2.0
            bd["dilution"] = -2.0
            break

    # 7. table cell_answer_slot 은 raw_cell 이 의미있는 힌트를 담을 수 있음
    if q.type == "table_cell_answer_slot":
        raw = (q.extras or {}).get("raw_cell", "") if hasattr(q, "extras") else ""
        if raw and re.search(r"\d", raw):      # 숫자 힌트 (연도·금액·TRL 등)
            score += 0.5
            bd["numeric_hint"] = 0.5

    return RankedQuestion(
        q_id=q.id, seq=q.seq, title=q.title, type=q.type,
        score=round(score, 3), breakdown=bd,
    )


def rank_questions(
    questions: Iterable,
    stage: str,
    *,
    descending: bool = True,
) -> list[RankedQuestion]:
    """전체 질문 리스트에 스코어 부여 + 정렬.

    동점 tiebreak: 원래 문서 순서(q.seq) 유지 → 섹션 흐름을 최대한 보존.
    """
    ranked = [score_question(q, stage) for q in questions]
    ranked.sort(
        key=lambda r: (-r.score if descending else r.score, r.seq),
    )
    return ranked


# ---------- 선택: G 관련성 -------------------------------------------------


def rank_with_rag(
    questions: Iterable,
    stage: str,
    *,
    g_url: str = "http://100.79.251.53:9999",
    namespace: str | None = None,
    top_k: int = 3,
    min_score: float = 0.3,
    timeout_s: float = 4.0,
    descending: bool = True,
) -> list[RankedQuestion]:
    """heuristic + G 관련성 가산.

    각 질문 → G `/search/hybrid` 쿼리 → top_k hit 중 min_score 이상인 것 수 * 1.0 가산.
    fact 가 0 개면 답할 재료가 없으므로 -3 감점 (내부 대체 또는 생략 판단 보조).

    N 개 질문 = N RPC → 시간 소요. 급하면 heuristic 만 쓰고 이건 GP_RANK_RAG=on 때만.
    """
    import json
    import urllib.error
    import urllib.request

    def _search(q_text: str) -> list[dict]:
        body = {"query": q_text[:300], "top_k": top_k}
        if namespace:
            body["namespace"] = namespace
        req = urllib.request.Request(
            f"{g_url.rstrip('/')}/search/hybrid",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            return []
        if isinstance(d, list):
            return d
        return d.get("results") or d.get("hits") or []

    out: list[RankedQuestion] = []
    for q in questions:
        r = score_question(q, stage)
        hits = _search(q.title)
        eligible = [h for h in hits if float(h.get("score") or 0.0) >= min_score]
        if eligible:
            boost = min(float(len(eligible)), 3.0)
            r.score = round(r.score + boost, 3)
            r.breakdown["g_rag_hits"] = boost
        else:
            r.score = round(r.score - 3.0, 3)
            r.breakdown["g_rag_missing"] = -3.0
        out.append(r)
    out.sort(key=lambda r: (-r.score if descending else r.score, r.seq))
    return out
