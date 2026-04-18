"""E2E — fake jw 바이너리 + bin/jw shim 전체 흐름.

shim 이 원본 위치 감지 → 위임 실행 → state/perf 자동 갱신까지 검증한다.
실제 네트워크·gateway 불필요.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LC_BIN = REPO_ROOT / "bin"


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    """tmp_path/fake_bin 에 fake jw/re 를 만들고 PATH 앞쪽에 주입한다."""
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    for name in ("jw", "re"):
        script = fake_bin / name
        script.write_text(
            "#!/bin/sh\n"
            f'echo "fake {name} $@"\n'
            'exit "${LC_FAKE_EXIT:-0}"\n'
        )
        script.chmod(0o755)

    env = os.environ.copy()
    # shim 이 원본 jw 를 찾을 때 fake_bin 이 잡히도록 lc_bin 뒤에 배치
    env["PATH"] = f"{LC_BIN}:{fake_bin}:{env['PATH']}"
    # LC 내부 데이터 디렉토리도 격리
    env["LC_DATA_DIR"] = str(tmp_path / "lc-data")
    return env


def test_shim_delegates_and_records_state(tmp_path: Path, fake_env: dict[str, str]) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    result = subprocess.run(
        [str(LC_BIN / "jw"), "idea", "테스트 주제"],
        cwd=project,
        env=fake_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "fake jw idea 테스트 주제" in result.stdout

    # state.json 이 자동 기록됨
    state_path = project / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["last_action"] == "jw idea"
    assert state["last_exit_code"] == 0

    # perf 로그도 append
    perf_dir = project / ".perf"
    perf_files = list(perf_dir.glob("log-*.jsonl"))
    assert len(perf_files) == 1
    lines = perf_files[0].read_text().strip().splitlines()
    assert any("jw idea" in line for line in lines)


def test_shim_propagates_nonzero_exit(tmp_path: Path, fake_env: dict[str, str]) -> None:
    project = tmp_path / "proj2"
    project.mkdir()
    env = dict(fake_env)
    env["LC_FAKE_EXIT"] = "3"
    result = subprocess.run(
        [str(LC_BIN / "re"), "lab-note"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 3

    state = json.loads((project / "state.json").read_text())
    assert state["last_action"] == "re lab-note"
    assert state["last_exit_code"] == 3


def test_shim_multi_call_accumulates_perf(tmp_path: Path, fake_env: dict[str, str]) -> None:
    project = tmp_path / "proj3"
    project.mkdir()
    for sub in ("idea", "spec", "proposal"):
        subprocess.run(
            [str(LC_BIN / "jw"), sub, "x"],
            cwd=project,
            env=fake_env,
            check=True,
            timeout=30,
            capture_output=True,
        )
    perf_files = list((project / ".perf").glob("log-*.jsonl"))
    lines = perf_files[0].read_text().strip().splitlines()
    actions = [json.loads(l)["action"] for l in lines]
    assert actions == ["jw idea", "jw spec", "jw proposal"]
