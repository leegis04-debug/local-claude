"""Selector loop 테스트 — mock OllamaChatClient 로 Ollama 비의존.

목표: 수렴 로직, 판단기 병렬 처리, system prompt 전달 검증.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _FakeGClient:
    """Gateway 호출 없이 static fused hits 반환."""

    def __init__(self, hits: list[dict]):
        self._hits = hits
        self.http = MagicMock()
        self.http.get.return_value.raise_for_status = lambda: None
        self.http.get.return_value.json = lambda: []   # procedure patterns 없음

    def fused_search(self, goal: str, **kwargs):
        return list(self._hits)


def test_run_selector_convergence(monkeypatch):
    from gstar.projection import selector_loop as sl

    hits = [
        {"node_id": f"n{i}", "text": f"후보 {i} — GraphRAG 관련 내용" if i < 5 else f"후보 {i} — 완전 무관", "score": 0.8, "origin": "g", "namespace": "x"}
        for i in range(10)
    ]
    gclient = _FakeGClient(hits)

    # OllamaChatClient 대체: "GraphRAG" 포함 시 "관련", 아니면 "무관"
    class _StubClient:
        def __init__(self, *a, **kw):
            pass

        def judge(self, *, system: str, prompt: str) -> str:
            return "관련" if "GraphRAG 관련" in prompt else "무관"

    monkeypatch.setattr(sl, "OllamaChatClient", _StubClient)
    # G_TRACE_RETRIEVAL off — network 호출 막기
    monkeypatch.setenv("G_TRACE_RETRIEVAL", "off")

    result = sl.run_selector(
        "GraphRAG 목표",
        gclient=gclient,
        top_k_final=10,
        candidate_k=10,
        max_iter=3,
        workers=2,
    )

    # 5 relevant 예상 (i < 5 만 "GraphRAG 관련" 포함)
    assert len(result.final_facts) == 5
    assert len(result.iterations) >= 1
    # 첫 iter 에 이미 top_k_final=10 >= relevant=5 이므로 break
    assert result.iterations[0].relevant == 5


def test_run_selector_empty_hits(monkeypatch):
    from gstar.projection import selector_loop as sl

    gclient = _FakeGClient([])
    monkeypatch.setenv("G_TRACE_RETRIEVAL", "off")

    class _StubClient:
        def __init__(self, *a, **kw):
            pass

        def judge(self, *, system: str, prompt: str) -> str:
            return "무관"

    monkeypatch.setattr(sl, "OllamaChatClient", _StubClient)

    result = sl.run_selector("no hits", gclient=gclient, top_k_final=5, candidate_k=5, max_iter=2)
    assert result.final_facts == []
