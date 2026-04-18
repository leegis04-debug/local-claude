from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from local_claude.infra import gateway as gateway_mod


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_load_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASST_TOKEN", "abc123")
    assert gateway_mod.load_token() == "abc123"


def test_load_token_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASST_TOKEN", raising=False)
    cfg = tmp_path / "config"
    cfg.write_text("# 주석\nASST_TOKEN=from-file\nFOO=bar\n")
    monkeypatch.setattr("local_claude.config.GATEWAY_TOKEN_CONFIG", cfg)
    assert gateway_mod.load_token() == "from-file"


def test_health_success_direct() -> None:
    received: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        received["url"] = str(req.url)
        received["headers"] = dict(req.headers)
        return httpx.Response(200, json={"ok": True})

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    resp = gw.health()
    assert resp.ok is True
    assert resp.status_code == 200
    assert resp.via == "direct"
    assert received["headers"]["x-auth-token"] == "T"
    # Bearer 절대 사용 안 함
    assert "authorization" not in received["headers"]


def test_search_hybrid_uses_correct_endpoint_and_default_exclude() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["method"] = req.method
        import json

        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"hits": []})

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    resp = gw.search_hybrid("스마트서비스")
    assert resp.ok is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/search/hybrid"  # /search 아님!
    assert captured["body"]["query"] == "스마트서비스"
    assert captured["body"]["exclude_sources"] == ["govsupport"]  # 디폴트


def test_search_hybrid_explicit_include_govsupport() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"hits": []})

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    gw.search_hybrid("X", exclude_sources=[])  # 명시적 비활성
    assert "exclude_sources" not in captured["body"]


def test_network_failure_then_tunnel_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """direct 에서 ConnectError → 터널 시도 → localhost 성공 케이스."""
    tunnel_called: list[bool] = []

    def fake_tunnel_up() -> object:
        tunnel_called.append(True)

        class S:
            listening: dict[int, bool] = {}
            all_up = True

        return S()

    monkeypatch.setattr("local_claude.infra.gateway.tunnel.up", fake_tunnel_up)

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "100.79.251.53":
            raise httpx.ConnectError("network unreachable", request=req)
        # localhost 는 성공
        return httpx.Response(200, json={"ok": True, "via": "tunnel"})

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    resp = gw.health()
    assert resp.ok is True
    assert resp.via == "tunnel"
    assert tunnel_called == [True]


def test_both_fail_returns_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("local_claude.infra.gateway.tunnel.up", lambda: None)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    resp = gw.health()
    assert resp.ok is False
    assert resp.via == "skipped"
    assert "2단계" in (resp.error or "")


def test_auto_tunnel_disabled_skips_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_up() -> object:
        called.append(True)

    monkeypatch.setattr("local_claude.infra.gateway.tunnel.up", fake_up)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=req)

    gw = gateway_mod.Gateway(token="T", auto_tunnel=False, _transport=_transport(handler))
    resp = gw.health()
    assert resp.via == "skipped"
    assert called == []


def test_graph_query_and_mcp_proxy_shapes() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"ok": True})

    gw = gateway_mod.Gateway(token="T", _transport=_transport(handler))
    gw.graph_query("MATCH (n) RETURN n LIMIT 1")
    gw.mcp_jw("validate_step", {"step": "idea", "content": "X"})
    gw.mcp_doc("critique_thought", {"thought": "Y"})

    methods_paths = [(m, p) for m, p, _ in seen]
    assert methods_paths == [
        ("POST", "/graph/query"),
        ("POST", "/mcp/jw/validate_step"),
        ("POST", "/mcp/doc/critique_thought"),
    ]
    assert seen[0][2]["cypher"].startswith("MATCH")
    assert seen[1][2] == {"step": "idea", "content": "X"}
