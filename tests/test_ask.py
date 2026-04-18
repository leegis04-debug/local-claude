from __future__ import annotations

import subprocess

import httpx
import pytest

from local_claude import ask as ask_mod
from local_claude.infra.gateway import Gateway


def test_ask_empty_prompt_fails() -> None:
    result = ask_mod.ask("")
    assert result.ok is False
    assert "비어" in (result.error or "")


def test_ask_invokes_ask_gemma(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list] = {"cmds": [], "inputs": []}

    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=180, check=False):  # noqa: A002
        captured["cmds"].append(cmd)
        captured["inputs"].append(input)

        class R:
            returncode = 0
            stdout = "답변 본문"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.ask.shutil.which", lambda _n: "/u/b/ask-gemma")

    r = ask_mod.ask("질문")
    assert r.ok is True
    assert r.text == "답변 본문"
    assert r.model == "gemma4:e4b"
    assert "--deep" not in captured["cmds"][0]


def test_ask_deep_uses_a4b(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(cmd, input=None, **kwargs):  # noqa: A002
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "깊은 답"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.ask.shutil.which", lambda _n: "/u/b/ask-gemma")

    r = ask_mod.ask("복잡한 질문", deep=True)
    assert r.ok is True
    assert r.model == "gemma4:a4b"
    assert "--deep" in captured["cmd"]


def test_ask_rag_prepends_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAG 모드: gateway /search/hybrid 결과가 프롬프트 앞에 [근거] 블록으로 붙는지."""
    # Gateway mock — hits 반환
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {"text": "근거 1 본문", "source": "projA"},
                    {"text": "근거 2 본문", "source": "projB"},
                ]
            },
        )

    class _FakeGateway(Gateway):
        def __init__(self, *a, **k):
            super().__init__(token="T", _transport=httpx.MockTransport(handler))

    monkeypatch.setattr("local_claude.ask.Gateway", _FakeGateway)

    captured: dict = {}

    def fake_run(cmd, input=None, **kwargs):  # noqa: A002
        captured["input"] = input

        class R:
            returncode = 0
            stdout = "RAG 기반 답변"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.ask.shutil.which", lambda _n: "/u/b/ask-gemma")

    r = ask_mod.ask("테스트 질문", rag=True)
    assert r.ok is True
    assert r.rag_evidence is not None
    assert len(r.rag_evidence) == 2
    # ask-gemma stdin 에 근거 블록이 프롬프트 앞에 붙었는지
    assert "[근거]" in captured["input"]
    assert "근거 1 본문" in captured["input"]
    assert "테스트 질문" in captured["input"]


def test_ask_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    r = ask_mod.ask("x")
    assert r.ok is False
    assert "ask-gemma" in (r.error or "")


def test_ask_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ask-gemma", timeout=1)

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    r = ask_mod.ask("x", timeout_s=1)
    assert r.ok is False
    assert "타임아웃" in (r.error or "")


def test_ask_rag_failure_falls_back_without_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """gateway 가 실패해도 질문 자체는 진행 — evidence=None 로."""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    class _FakeGateway(Gateway):
        def __init__(self, *a, **k):
            super().__init__(token="T", auto_tunnel=False, _transport=httpx.MockTransport(handler))

    monkeypatch.setattr("local_claude.ask.Gateway", _FakeGateway)

    captured: dict = {}

    def fake_run(cmd, input=None, **kwargs):  # noqa: A002
        captured["input"] = input

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.ask.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.ask.shutil.which", lambda _n: "/u/b/ask-gemma")

    r = ask_mod.ask("테스트", rag=True)
    assert r.ok is True
    # 근거 못 얻었으므로 프롬프트 그대로 전달
    assert captured["input"] == "테스트"
    assert r.rag_evidence is None
