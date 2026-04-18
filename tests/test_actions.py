from __future__ import annotations

import subprocess
from typing import Any

import httpx
import pytest

from local_claude.actions import ask_deep, critique, search_graph, search_rag, validate
from local_claude.infra.gateway import Gateway


def _gateway(handler) -> Gateway:
    return Gateway(token="T", _transport=httpx.MockTransport(handler))


# ── search_rag ──────────────────────────────────────────────────────────────

def test_search_rag_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        assert body["query"] == "스마트서비스"
        assert body["exclude_sources"] == ["govsupport"]
        return httpx.Response(200, json={"hits": [{"id": 1}]})

    action = search_rag.SearchRagAction()
    res = action.execute({"query": "스마트서비스", "top_k": "5"}, gateway=_gateway(handler))
    assert res.ok is True
    assert res.output == {"hits": [{"id": 1}]}
    assert res.meta["via"] == "direct"


def test_search_rag_uses_body_as_query() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        assert json.loads(req.content)["query"] == "본문-쿼리"
        return httpx.Response(200, json={"hits": []})

    action = search_rag.SearchRagAction()
    res = action.execute({"_body": "본문-쿼리"}, gateway=_gateway(handler))
    assert res.ok is True


def test_search_rag_empty_query_fails() -> None:
    action = search_rag.SearchRagAction()
    res = action.execute({}, gateway=_gateway(lambda r: httpx.Response(200)))
    assert res.ok is False
    assert "query" in (res.error or "")


# ── search_graph ────────────────────────────────────────────────────────────

def test_search_graph_ok_read_query() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        assert body["cypher"].startswith("MATCH")
        return httpx.Response(200, json={"rows": []})

    action = search_graph.SearchGraphAction()
    res = action.execute({"cypher": "MATCH (n) RETURN n"}, gateway=_gateway(handler))
    assert res.ok is True


def test_search_graph_blocks_destructive() -> None:
    action = search_graph.SearchGraphAction()
    for bad in ["CREATE (n)", "DETACH DELETE n", "SET n.x=1", "DROP INDEX"]:
        res = action.execute({"cypher": f"MATCH (n) {bad}"}, gateway=_gateway(lambda r: httpx.Response(200)))
        assert res.ok is False
        assert "파괴적" in (res.error or "")


# ── ask_deep ────────────────────────────────────────────────────────────────

def test_ask_deep_invokes_ask_gemma(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=60, check=False):  # noqa: A002
        captured["cmd"] = cmd
        captured["input"] = input

        class R:
            returncode = 0
            stdout = "a4b 응답"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.actions.ask_deep.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.actions.ask_deep.shutil.which", lambda _n: "/u/b/ask-gemma")

    res = ask_deep.AskDeepAction().execute({"prompt": "복잡한 문제", "rag": "true"})
    assert res.ok is True
    assert res.output == "a4b 응답"
    assert "--deep" in captured["cmd"]
    assert "--rag" in captured["cmd"]
    assert captured["input"] == "복잡한 문제"


def test_ask_deep_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("local_claude.actions.ask_deep.subprocess.run", fake_run)
    res = ask_deep.AskDeepAction().execute({"prompt": "q"})
    assert res.ok is False
    assert "ask-gemma" in (res.error or "")


def test_ask_deep_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ask-gemma", timeout=1)

    monkeypatch.setattr("local_claude.actions.ask_deep.subprocess.run", fake_run)
    res = ask_deep.AskDeepAction().execute({"prompt": "q"})
    assert res.ok is False
    assert "타임아웃" in (res.error or "")


def test_ask_deep_empty_prompt_fails() -> None:
    res = ask_deep.AskDeepAction().execute({})
    assert res.ok is False


# ── validate ────────────────────────────────────────────────────────────────

def test_validate_routes_to_mcp_jw() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"passed": True})

    res = validate.ValidateAction().execute(
        {"tool": "validate_step", "args": {"step": "idea"}},
        gateway=_gateway(handler),
    )
    assert res.ok is True
    assert seen[0][0] == "/mcp/jw/validate_step"
    assert seen[0][1] == {"step": "idea"}


def test_validate_promotes_body_to_content() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    validate.ValidateAction().execute(
        {"_body": "본문 스니펫", "step": "spec"},
        gateway=_gateway(handler),
    )
    assert seen[0]["content"] == "본문 스니펫"
    assert seen[0]["step"] == "spec"


# ── critique ────────────────────────────────────────────────────────────────

def test_critique_routes_to_mcp_doc() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json={"ok": True})

    critique.CritiqueAction().execute(
        {"tool": "critique_thought", "args": {"thought": "X"}},
        gateway=_gateway(handler),
    )
    assert seen == ["/mcp/doc/critique_thought"]


def test_critique_promotes_body_to_thought() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    critique.CritiqueAction().execute({"_body": "생각"}, gateway=_gateway(handler))
    assert seen[0]["thought"] == "생각"
