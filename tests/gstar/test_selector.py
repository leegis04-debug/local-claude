"""선택 루프 — mock Judge 로 종료 조건·안정화 검증."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from gstar.config import Weights
from gstar.embedding import DummyEmbedder
from gstar.gravity.field import add_goal
from gstar.schema import Node
from gstar.selector.gemma_client import parse_yesno
from gstar.selector.selection_loop import run_selection
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


# ---------------- Mock Judge ----------------


@dataclass
class ScriptedJudge:
    """지식 조각(문서 텍스트) 부분에 키워드가 있으면 '예'. goal 문구는 무시."""

    keep_keywords: list[str]
    calls: int = 0

    def judge(self, *, system: str, prompt: str) -> str:
        self.calls += 1
        # _build_prompt 는 "지식 조각: <text>" 라인을 포함함. 그 부분만 본다.
        snippet = prompt
        if "지식 조각:" in prompt:
            after = prompt.split("지식 조각:", 1)[1]
            snippet = after.split("이 조각이", 1)[0]
        for kw in self.keep_keywords:
            if kw in snippet:
                return "예"
        return "아니오"


def _store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "g.duckdb")


def _seed_nodes(store: DuckStore, faiss: FaissStore, embedder) -> list[Node]:
    texts = [
        "RAG 는 LLM 품질을 개선한다",
        "RAG 는 검색과 생성을 결합한다",
        "고양이는 포유류이다",
        "자동차 엔진은 내연기관이다",
        "OptiREC 는 RAG 기반 추천 시스템이다",
        "블록체인은 분산 원장이다",
    ]
    nodes = [Node(kind="fact", text=t, source_namespace="test") for t in texts]
    for n in nodes:
        store.insert_node(n)
    vecs = embedder.encode(texts)
    faiss.add_batch([n.id for n in nodes], vecs)
    return nodes


@pytest.fixture
def env(tmp_path: Path):
    embedder = DummyEmbedder(dim=16)
    store = _store(tmp_path)
    faiss = FaissStore(tmp_path / "emb.faiss", dim=16)
    nodes = _seed_nodes(store, faiss, embedder)
    goal, _ = add_goal("RAG 사업계획서", "proposal", store, faiss, embedder)
    yield store, faiss, embedder, goal, nodes
    store.close()


def test_selection_converges_when_judge_is_stable(env):
    store, faiss, _, goal, nodes = env
    judge = ScriptedJudge(keep_keywords=["RAG"])
    weights = Weights(
        w_rel=1.0, w_rec=0.0, w_cent=0.0, w_ver=0.0, w_pur=0.0, w_stab=0.0,
    )
    result = run_selection(
        goal, store, faiss, weights, judge,
        top_k=10, max_cycles=5, stable_window=2,
    )
    assert result.converged
    # RAG 포함 노드 3개가 kept
    kept_texts = [store.get_node(nid).text for nid in result.final_kept]
    assert all("RAG" in t for t in kept_texts)
    assert len(kept_texts) == 3


def test_selection_log_reflects_cycles(env):
    store, faiss, _, goal, _ = env
    judge = ScriptedJudge(keep_keywords=["RAG"])
    weights = Weights(
        w_rel=1.0, w_rec=0.0, w_cent=0.0, w_ver=0.0, w_pur=0.0, w_stab=0.0,
    )
    result = run_selection(
        goal, store, faiss, weights, judge,
        top_k=10, max_cycles=4, stable_window=2,
    )
    assert len(result.cycles) >= 2
    # 각 cycle 결과 합계가 candidates 수 맞는지
    for cr in result.cycles:
        assert len(cr.kept) + len(cr.dropped) + len(cr.undecided) == cr.candidates


def test_selection_respects_max_cycles_when_unstable(env):
    store, faiss, _, goal, _ = env

    # 같은 prompt 에 대해 사이클마다 반대 답 → 절대 안정화 안 됨
    class Flip:
        def __init__(self):
            self.seen: dict[str, int] = {}

        def judge(self, *, system: str, prompt: str) -> str:
            count = self.seen.get(prompt, 0)
            self.seen[prompt] = count + 1
            return "예" if count % 2 == 0 else "아니오"

    judge = Flip()
    weights = Weights(w_rel=1.0, w_rec=0.0, w_cent=0.0, w_ver=0.0, w_pur=0.0, w_stab=0.0)
    result = run_selection(
        goal, store, faiss, weights, judge,
        top_k=6, max_cycles=3, stable_window=2,
    )
    assert len(result.cycles) == 3
    assert not result.converged


def test_parse_yesno_covers_common_forms():
    assert parse_yesno("예") is True
    assert parse_yesno("네 맞습니다") is True
    assert parse_yesno("아니오") is False
    assert parse_yesno("Yes") is True
    assert parse_yesno("no") is False
    assert parse_yesno("글쎄요") is None
    assert parse_yesno("") is None
