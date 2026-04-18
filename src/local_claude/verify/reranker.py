"""Reranker — 원 RAG 결과를 쿼리 관련도로 재정렬.

세 가지 구현:
- NoopReranker: 원 순서·점수 보존.
- HeuristicReranker: 쿼리-문서 토큰 containment 와 원 score 의 가중 합 (zero-dep).
- LLMReranker: ask-gemma 에 "1-10점" 질의 (옵션, subprocess 호출).

향후 gateway /rerank 엔드포인트가 생기면 GatewayReranker 를 추가만 하면 된다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .. import config
from ._tokenize import containment

Document = dict[str, Any]
"""원자적 근거: {"text": str, "score": float, "source"?: str, "id"?: Any, ...}"""


@dataclass
class RankedDocument:
    doc: Document
    score: float
    rank: int
    original_rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc": self.doc,
            "score": round(self.score, 4),
            "rank": self.rank,
            "original_rank": self.original_rank,
        }


class Reranker(Protocol):
    name: str

    def rerank(
        self,
        query: str,
        docs: Iterable[Document],
        *,
        top_k: int | None = None,
    ) -> list[RankedDocument]: ...  # pragma: no cover


# ── 공통 ────────────────────────────────────────────────────────────────────

def _doc_text(doc: Document) -> str:
    for key in ("text", "content", "body", "snippet", "passage"):
        value = doc.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(doc, ensure_ascii=False)


def _original_score(doc: Document) -> float:
    for key in ("score", "_score", "rrf_score", "rank_score"):
        value = doc.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _finalize(items: list[tuple[Document, float, int]], top_k: int | None) -> list[RankedDocument]:
    ordered = sorted(items, key=lambda t: t[1], reverse=True)
    if top_k is not None:
        ordered = ordered[:top_k]
    return [
        RankedDocument(doc=doc, score=score, rank=i + 1, original_rank=orig)
        for i, (doc, score, orig) in enumerate(ordered)
    ]


# ── 1) Noop ─────────────────────────────────────────────────────────────────

@dataclass
class NoopReranker:
    name: str = "noop"

    def rerank(
        self,
        query: str,
        docs: Iterable[Document],
        *,
        top_k: int | None = None,
    ) -> list[RankedDocument]:
        items = [(doc, _original_score(doc), i + 1) for i, doc in enumerate(docs)]
        # 원 순서 유지 — score 로 재정렬하지 않고 rank 만 부여.
        if top_k is not None:
            items = items[:top_k]
        return [
            RankedDocument(doc=d, score=s, rank=i + 1, original_rank=orig)
            for i, (d, s, orig) in enumerate(items)
        ]


# ── 2) Heuristic (default) ──────────────────────────────────────────────────

@dataclass
class HeuristicReranker:
    """쿼리 토큰이 문서에 얼마나 담겼는지 + 원 score 를 선형결합.

    alpha: containment 가중치 (0..1). 1.0 이면 원 score 무시.
    """

    alpha: float = 0.7
    name: str = "heuristic"

    def rerank(
        self,
        query: str,
        docs: Iterable[Document],
        *,
        top_k: int | None = None,
    ) -> list[RankedDocument]:
        alpha = max(0.0, min(1.0, self.alpha))
        items: list[tuple[Document, float, int]] = []
        for i, doc in enumerate(docs):
            text = _doc_text(doc)
            overlap = containment(query, text)
            orig = _original_score(doc)
            # orig 의 크기가 [0,1] 을 벗어나도 상대 순서는 보존.
            score = alpha * overlap + (1 - alpha) * orig
            items.append((doc, score, i + 1))
        return _finalize(items, top_k)


# ── 3) LLM (ask-gemma) ──────────────────────────────────────────────────────

_SCORE_PROMPT = """너는 RAG 리랭커다. 아래 질의에 대해 문서가 얼마나 관련 있는지
**정수 1~10** 한 줄로만 답하라. 다른 말 금지.

[질의]
{query}

[문서]
{text}
"""

_SCORE_RE = re.compile(r"\b([1-9]|10)\b")


@dataclass
class LLMReranker:
    """ask-gemma 에 문서별 1~10 점수를 받아 재정렬. 맥북 e4b 디폴트 (빠름).

    실패하거나 파싱 안 되면 원 score 로 fallback.
    """

    timeout_s: int = 20
    deep: bool = False
    name: str = "llm"

    def _score_one(self, query: str, text: str) -> float | None:
        bin_path = shutil.which(config.ASK_GEMMA_BIN) or config.ASK_GEMMA_BIN
        cmd = [bin_path]
        if self.deep:
            cmd.append("--deep")
        prompt = _SCORE_PROMPT.format(query=query[:2000], text=text[:2000])
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        match = _SCORE_RE.search(result.stdout or "")
        if not match:
            return None
        return int(match.group(1)) / 10.0

    def rerank(
        self,
        query: str,
        docs: Iterable[Document],
        *,
        top_k: int | None = None,
    ) -> list[RankedDocument]:
        items: list[tuple[Document, float, int]] = []
        for i, doc in enumerate(docs):
            text = _doc_text(doc)
            score = self._score_one(query, text)
            if score is None:
                score = _original_score(doc)
            items.append((doc, score, i + 1))
        return _finalize(items, top_k)


# ── 팩토리 ──────────────────────────────────────────────────────────────────

def get_reranker(mode: str = "heuristic", **kwargs: Any) -> Reranker:
    mode = (mode or "heuristic").lower()
    if mode == "noop":
        return NoopReranker()
    if mode == "llm":
        return LLMReranker(**kwargs)
    return HeuristicReranker(**kwargs)
