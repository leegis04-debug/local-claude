"""run_cycle steps 필터 동작 테스트 (Phase H10 tick 분리)."""

from __future__ import annotations

from gstar.worker.cycle import (
    ALL_STEPS,
    HEAVY_STEPS,
    LIGHT_STEPS,
    _resolve_steps,
    run_cycle,
    set_paused,
)


def test_resolve_steps_default_light():
    assert _resolve_steps(None) == set(LIGHT_STEPS)


def test_resolve_steps_wildcard_full():
    assert _resolve_steps(["*"]) == set(ALL_STEPS)


def test_resolve_steps_explicit():
    assert _resolve_steps(["community"]) == {"community"}
    assert _resolve_steps(["code_repos", "procedures"]) == {"code_repos", "procedures"}


def test_resolve_steps_unknown_filtered():
    assert _resolve_steps(["bogus"]) == set()
    assert _resolve_steps(["community", "bogus"]) == {"community"}


def test_resolve_steps_empty_list_is_empty_not_light():
    # 명시적 [] 는 "아무것도 실행 X" 의도로 존중 (감시·no-op 용).
    assert _resolve_steps([]) == set()


def test_run_cycle_light_default(store):
    rep = run_cycle(store)
    assert rep.status == "success"
    assert sorted(rep.steps["_requested"]) == sorted(LIGHT_STEPS)
    # heavy step 결과는 없어야 함 (default light 라서 호출 자체 안 됨)
    for heavy in HEAVY_STEPS:
        assert heavy not in rep.steps


def test_run_cycle_respects_steps_filter(store):
    # code_repos + legacy_fact_bridge 는 env opt-in 없이 호출 자체가 안 되어야 함
    rep = run_cycle(store, steps=["community"])
    assert rep.status == "success"
    assert rep.steps["_requested"] == ["community"]
    assert "procedures" not in rep.steps
    assert "qdrant_mirror" not in rep.steps


def test_run_cycle_paused(store, tmp_path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    set_paused(True)
    try:
        rep = run_cycle(store)
        assert rep.status == "paused"
    finally:
        set_paused(False)
