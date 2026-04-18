from __future__ import annotations

import pytest

from local_claude.verify import reranker as rr


def test_noop_preserves_order_and_assigns_rank() -> None:
    docs = [
        {"text": "aaa", "score": 0.1},
        {"text": "zzz", "score": 0.9},
        {"text": "mmm", "score": 0.5},
    ]
    ranked = rr.NoopReranker().rerank("query", docs)
    assert [r.doc["text"] for r in ranked] == ["aaa", "zzz", "mmm"]
    assert [r.rank for r in ranked] == [1, 2, 3]
    assert [r.original_rank for r in ranked] == [1, 2, 3]


def test_heuristic_promotes_query_matching_document() -> None:
    docs = [
        {"text": "apple banana cherry"},
        {"text": "데이터 베이스 그래프"},
        {"text": "RAG 하이브리드 검색 지식그래프"},
    ]
    ranked = rr.HeuristicReranker(alpha=1.0).rerank("RAG 하이브리드 검색", docs)
    # 쿼리와 토큰 겹침이 가장 많은 마지막 문서가 1위여야 함.
    assert ranked[0].doc["text"].startswith("RAG")
    assert ranked[0].score > ranked[-1].score


def test_heuristic_mixes_original_score_when_alpha_less_than_one() -> None:
    docs = [
        {"text": "완전 무관한 문서", "score": 0.95},  # 원 score 가 매우 높음
        {"text": "쿼리 단어 포함 약간", "score": 0.1},
    ]
    # alpha=0.3 이면 원 score 비중 70% — 첫 문서가 이겨야.
    ranked = rr.HeuristicReranker(alpha=0.3).rerank("쿼리 단어", docs)
    assert ranked[0].doc["text"] == "완전 무관한 문서"


def test_heuristic_top_k_truncates() -> None:
    docs = [{"text": f"doc {i} 쿼리"} for i in range(5)]
    ranked = rr.HeuristicReranker().rerank("쿼리", docs, top_k=2)
    assert len(ranked) == 2
    assert [r.rank for r in ranked] == [1, 2]


def test_get_reranker_factory() -> None:
    assert isinstance(rr.get_reranker("noop"), rr.NoopReranker)
    assert isinstance(rr.get_reranker("heuristic"), rr.HeuristicReranker)
    assert isinstance(rr.get_reranker("unknown"), rr.HeuristicReranker)  # 디폴트
    assert isinstance(rr.get_reranker("llm"), rr.LLMReranker)


def test_llm_reranker_parses_score(monkeypatch: pytest.MonkeyPatch) -> None:
    scores = iter(["  8", "답변: 3", "관련성: 10/10"])

    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=20, check=False):  # noqa: A002
        class R:
            returncode = 0
            stdout = next(scores)
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.verify.reranker.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.verify.reranker.shutil.which", lambda _n: "/u/b/ask-gemma")

    docs = [{"text": "A"}, {"text": "B"}, {"text": "C"}]
    ranked = rr.LLMReranker().rerank("q", docs)
    # 순서: C(10) > A(8) > B(3)
    assert [r.doc["text"] for r in ranked] == ["C", "A", "B"]


def test_llm_reranker_falls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("local_claude.verify.reranker.subprocess.run", fake_run)
    docs = [
        {"text": "A", "score": 0.1},
        {"text": "B", "score": 0.9},
    ]
    ranked = rr.LLMReranker().rerank("q", docs)
    # 원 score 로 fallback → B 가 먼저
    assert ranked[0].doc["text"] == "B"
