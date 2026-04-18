from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from local_claude.actions.base import REGISTRY, ActionResult, register
from local_claude.infra.gateway import Gateway
from local_claude.orchestrator import executor, loop, parser
from local_claude.perf import logger as perf_logger
from local_claude.state import compressor as state_mod


def _gateway(handler) -> Gateway:
    return Gateway(token="T", _transport=httpx.MockTransport(handler))


def test_dispatch_unknown_action_returns_error(tmp_path: Path) -> None:
    ex = executor.dispatch(("nope", {}), project_dir=tmp_path)
    assert ex.result.ok is False
    assert "unknown action" in (ex.result.error or "")


def test_dispatch_records_perf_and_state(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": []})

    # 직접 REGISTRY 팩토리를 gateway 주입 가능하게 우회: 전용 액션을 등록.
    class _Fake:
        name = "fake_ok"

        def execute(self, payload, **_):
            return ActionResult(ok=True, output="yes")

    REGISTRY["fake_ok"] = lambda: _Fake()
    try:
        ex = executor.dispatch(("fake_ok", {}), project_dir=tmp_path)
        assert ex.result.ok is True
    finally:
        del REGISTRY["fake_ok"]

    # perf 로그가 한 건 생성됐어야 함
    log = perf_logger.log_path(tmp_path)
    assert log.exists()
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "fake_ok"
    assert entry["exit_code"] == 0
    assert entry["validation"] == "pass"

    # state 에도 last_action 이 박혔어야 함
    s = state_mod.load(tmp_path)
    assert s.last_action == "fake_ok"
    assert s.last_exit_code == 0


def test_dispatch_catches_handler_exception(tmp_path: Path) -> None:
    class _Boom:
        name = "boom"

        def execute(self, payload, **_):
            raise RuntimeError("kaboom")

    REGISTRY["boom"] = lambda: _Boom()
    try:
        ex = executor.dispatch(("boom", {}), project_dir=tmp_path)
    finally:
        del REGISTRY["boom"]
    assert ex.result.ok is False
    assert "RuntimeError" in (ex.result.error or "")
    assert "kaboom" in (ex.result.error or "")


def test_dispatch_all_stop_on_failure(tmp_path: Path) -> None:
    class _Bad:
        name = "bad"

        def execute(self, payload, **_):
            return ActionResult(ok=False, error="nope")

    class _Good:
        name = "good"

        def execute(self, payload, **_):
            return ActionResult(ok=True)

    REGISTRY["bad"] = lambda: _Bad()
    REGISTRY["good"] = lambda: _Good()
    try:
        parsed = [
            parser.ParsedAction(name="bad", payload={}),
            parser.ParsedAction(name="good", payload={}),
        ]
        results = executor.dispatch_all(parsed, project_dir=tmp_path, stop_on_failure=True)
    finally:
        del REGISTRY["bad"]
        del REGISTRY["good"]
    assert len(results) == 1
    assert results[0].name == "bad"


# ── loop ────────────────────────────────────────────────────────────────────

def test_loop_verifies_success(tmp_path: Path) -> None:
    class _V:
        name = "validate"

        def execute(self, payload, **_):
            return ActionResult(ok=True)

    REGISTRY["validate"] = lambda: _V()
    try:
        result = loop.run(
            "<validate tool=\"validate_step\"/>",
            project_dir=tmp_path,
        )
    finally:
        del REGISTRY["validate"]
    assert result.verified is True
    assert result.attempts == 1
    assert [ex.name for ex in result.executed] == ["validate"]


def test_loop_retries_with_planner(tmp_path: Path) -> None:
    # 첫 회는 fail, 재시도 회는 ok 로 교체되는 시나리오.
    state = {"count": 0}

    class _V:
        name = "validate"

        def execute(self, payload, **_):
            state["count"] += 1
            return ActionResult(ok=state["count"] >= 2)

    REGISTRY["validate"] = lambda: _V()

    def planner(prev: str, executed: list[executor.ExecutedAction]) -> str:
        assert prev == "<validate/>"
        return "<validate/>"

    try:
        result = loop.run(
            "<validate/>",
            project_dir=tmp_path,
            max_retries=2,
            planner=planner,
        )
    finally:
        del REGISTRY["validate"]
    assert result.verified is True
    assert result.attempts == 2
    assert len(result.executed) == 2


def test_loop_no_actions_short_circuit(tmp_path: Path) -> None:
    result = loop.run("그냥 텍스트. 태그 없음.", project_dir=tmp_path)
    assert result.executed == []
    assert result.verified is False
