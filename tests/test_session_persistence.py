"""세션 간 상태 지속성 — 여러 프로세스 경계 걸친 state/memory/perf 누적.

`lc` CLI 를 subprocess 로 반복 호출해 실제 프로세스 경계 경유 영속성 확인.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LC_BIN = REPO_ROOT / "bin" / "lcai"


@pytest.fixture
def isolated_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["LC_DATA_DIR"] = str(tmp_path / "lc-data")
    # 테스트에서 PYTHONPATH 가 필요 — bin/lcai 는 자체 설정하지만 환경 유지.
    return env


def _lc(args: list[str], project: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(LC_BIN), "-p", str(project), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_state_accumulates_across_calls(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    _lc(["state", "update", "--current-goal", "G1"], project, isolated_env)
    _lc(["state", "update", "--failure", "첫 실패"], project, isolated_env)
    _lc(["state", "update", "--decision", "D1", "--decision", "D2"], project, isolated_env)
    _lc(["state", "update", "--failure", "두 번째 실패"], project, isolated_env)

    show = _lc(["state", "show"], project, isolated_env)
    assert show.returncode == 0
    state = json.loads(show.stdout)
    assert state["current_goal"] == "G1"
    assert state["recent_failures"] == ["첫 실패", "두 번째 실패"]
    assert state["decisions"] == ["D1", "D2"]


def test_memory_persists_across_calls(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    _lc(["memory", "remember", "결정 A", "--category", "decision"], project, isolated_env)
    _lc(["memory", "remember", "결정 A", "--category", "decision"], project, isolated_env)  # 중복
    _lc(["memory", "remember", "사용자 선호 X", "--scope", "user", "--category", "preference"], project, isolated_env)

    recall = _lc(["memory", "recall"], project, isolated_env)
    assert recall.returncode == 0
    bundle = json.loads(recall.stdout)

    proj_texts = [e["text"] for e in bundle["project"]]
    assert proj_texts == ["결정 A"]  # dedup 동작
    # touch_count 가 증가했는지
    assert bundle["project"][0]["touch_count"] >= 1

    user_texts = [e["text"] for e in bundle["user"]]
    assert "사용자 선호 X" in user_texts


def test_perf_log_appends_across_calls(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    for action in ("jw idea", "jw structure", "jw spec"):
        _lc(
            ["perf", "log", "--action", action, "--duration-ms", "123", "--exit-code", "0"],
            project,
            isolated_env,
        )

    tail = _lc(["perf", "tail", "-n", "50"], project, isolated_env)
    assert tail.returncode == 0
    lines = [line for line in tail.stdout.splitlines() if line.startswith("{")]
    actions = [json.loads(l)["action"] for l in lines]
    assert actions == ["jw idea", "jw structure", "jw spec"]


def test_corrupt_state_auto_recovers(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    # 먼저 정상 생성
    _lc(["state", "update", "--current-goal", "G"], project, isolated_env)
    # 고의로 손상
    (project / "state.json").write_text("{ broken json")
    # 다음 호출에서 자동 복구
    result = _lc(["state", "show"], project, isolated_env)
    assert result.returncode == 0
    state = json.loads(result.stdout)
    assert state["current_goal"] == ""  # 손상 → 초기화
    assert (project / "state.json.corrupt").exists()


def test_status_and_doctor_run(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    # doctor 는 실제 gateway 호출을 포함 — 테스트에서는 짧은 timeout 강제.
    env = dict(isolated_env)
    env["LC_GATEWAY_TIMEOUT"] = "2"
    _lc(["state", "update", "--current-goal", "smoke"], project, env)

    status = _lc(["status"], project, env)
    assert status.returncode == 0
    payload = json.loads(status.stdout)
    assert payload["state"]["current_goal"] == "smoke"
    assert "key_files" in payload

    doctor = _lc(["doctor"], project, env)
    # doctor exit code 는 환경 의존 (gateway/ssh). JSON 파싱만 확인.
    report = json.loads(doctor.stdout)
    assert "checks" in report
    assert "summary" in report
    # 최소한 몇 가지 체크는 수행됨
    check_names = {c["name"] for c in report["checks"]}
    assert "state.json" in check_names
    assert any("memory" in n for n in check_names)


def test_tools_list_and_call(tmp_path: Path, isolated_env: dict[str, str]) -> None:
    """connect-ai 경로 스모크 — tools list + JSON payload call envelope."""
    project = tmp_path / "proj"
    project.mkdir()

    ls = _lc(["tools", "list"], project, isolated_env)
    assert ls.returncode == 0
    tools = json.loads(ls.stdout)["tools"]
    names = {t["name"] for t in tools}
    assert {"search_rag", "run_test", "cross_check"}.issubset(names)

    # run_test 호출 — 프로젝트 감지 실패(project 는 빈 dir)이라 ok=False 이지만
    # envelope 포맷은 유지.
    result = subprocess.run(
        [str(LC_BIN), "-p", str(project), "tools", "call", "run_test"],
        input=json.dumps({"cmd": "/bin/sh -c 'exit 0'", "cwd": str(project)}),
        env=isolated_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    envelope = json.loads(result.stdout)
    assert "ok" in envelope
    assert "name" in envelope and envelope["name"] == "run_test"
    assert "output" in envelope
    assert "meta" in envelope
    assert "duration_ms" in envelope
    assert envelope["ok"] is True
