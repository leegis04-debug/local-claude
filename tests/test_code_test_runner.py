from __future__ import annotations

import json
from pathlib import Path

from local_claude.code import test_runner


def test_detect_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    cmd, detected = test_runner.detect_command(tmp_path)
    assert detected == "pytest"
    assert cmd == ["python3", "-m", "pytest"]


def test_detect_npm(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}})
    )
    cmd, detected = test_runner.detect_command(tmp_path)
    assert detected == "npm"
    assert cmd == ["npm", "test"]


def test_detect_npm_without_test_script_falls_through(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}))
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    cmd, detected = test_runner.detect_command(tmp_path)
    assert detected == "cargo"


def test_detect_cargo(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    cmd, detected = test_runner.detect_command(tmp_path)
    assert detected == "cargo"


def test_detect_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\n")
    _, detected = test_runner.detect_command(tmp_path)
    assert detected == "go"


def test_detect_make(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\techo ok\n")
    _, detected = test_runner.detect_command(tmp_path)
    assert detected == "make"


def test_detect_none(tmp_path: Path) -> None:
    cmd, detected = test_runner.detect_command(tmp_path)
    assert cmd is None
    assert detected == "none"


def test_run_override_cmd_success(tmp_path: Path) -> None:
    result = test_runner.run(tmp_path, cmd="/bin/sh -c 'exit 0'")
    assert result.ok is True
    assert result.detected == "override"
    assert result.attempts == 1
    assert result.exit_code == 0


def test_run_override_cmd_failure_with_retries(tmp_path: Path) -> None:
    # 매번 실패 → retries=2 면 총 3회 시도.
    result = test_runner.run(tmp_path, cmd="/bin/sh -c 'exit 7'", retries=2)
    assert result.ok is False
    assert result.attempts == 3
    assert result.exit_code == 7


def test_run_none_detected_returns_error(tmp_path: Path) -> None:
    # 프로젝트 감지 실패 + cmd 없음
    result = test_runner.run(tmp_path)
    assert result.ok is False
    assert result.detected == "none"
    assert result.exit_code == 127


def test_run_timeout(tmp_path: Path) -> None:
    result = test_runner.run(
        tmp_path,
        cmd="/bin/sh -c 'sleep 5'",
        timeout_s=1,
    )
    assert result.ok is False
    assert "timeout" in result.stderr
