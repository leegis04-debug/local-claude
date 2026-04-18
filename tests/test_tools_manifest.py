"""connect-ai 백엔드 인터페이스 (lc tools) 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from local_claude.actions.base import REGISTRY, ActionResult
from local_claude.infra.gateway import Gateway
from local_claude.tools import manifest, runner


def test_manifest_list_returns_registered_actions() -> None:
    names = {t["name"] for t in manifest.list_tools()}
    # 등록된 action 중 manifest 가 있는 것만 반환
    assert "search_rag" in names
    assert "cross_check" in names
    assert "run_test" in names
    assert "ask_deep" in names


def test_manifest_get_and_schema_shape() -> None:
    spec = manifest.get("search_rag")
    assert spec is not None
    assert spec.name == "search_rag"
    assert "query" in spec.input_schema
    assert spec.input_schema["query"]["required"] is True
    assert len(spec.examples) >= 1


def test_manifest_get_unknown() -> None:
    assert manifest.get("does_not_exist") is None


def test_runner_unknown_tool_returns_envelope() -> None:
    env = runner.call("nope", {})
    assert env["ok"] is False
    assert "unknown tool" in (env["error"] or "")
    assert "available" in env["meta"]
    assert "name" in env and env["name"] == "nope"


def test_runner_envelope_shape_success(tmp_path: Path) -> None:
    class _Ok:
        name = "ok_tool"

        def execute(self, payload, **_):
            return ActionResult(ok=True, output={"echo": payload.get("x")})

    REGISTRY["ok_tool"] = lambda: _Ok()
    try:
        env = runner.call("ok_tool", {"x": 42}, project_dir=tmp_path)
    finally:
        del REGISTRY["ok_tool"]
    assert env["ok"] is True
    assert env["name"] == "ok_tool"
    assert env["output"] == {"echo": 42}
    assert env["error"] is None
    assert env["duration_ms"] >= 0


def test_runner_envelope_shape_failure(tmp_path: Path) -> None:
    class _Bad:
        name = "bad_tool"

        def execute(self, payload, **_):
            return ActionResult(ok=False, error="oops")

    REGISTRY["bad_tool"] = lambda: _Bad()
    try:
        env = runner.call("bad_tool", {}, project_dir=tmp_path)
    finally:
        del REGISTRY["bad_tool"]
    assert env["ok"] is False
    assert env["error"] == "oops"


def test_runner_routes_through_registered_action(tmp_path: Path) -> None:
    """runner 가 REGISTRY 팩토리를 통해 action 을 dispatch 하는지 — 경로 검증만."""
    captured: dict[str, Any] = {}

    class _Captured:
        name = "captured_tool"

        def execute(self, payload, **_):
            captured["payload"] = payload
            return ActionResult(ok=True, output={"got": payload})

    REGISTRY["captured_tool"] = lambda: _Captured()
    try:
        env = runner.call("captured_tool", {"query": "테스트", "top_k": 3}, project_dir=tmp_path)
    finally:
        del REGISTRY["captured_tool"]
    assert env["ok"] is True
    assert captured["payload"] == {"query": "테스트", "top_k": 3}
    assert env["output"] == {"got": {"query": "테스트", "top_k": 3}}
