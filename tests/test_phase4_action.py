from __future__ import annotations

from pathlib import Path

from local_claude.actions import run_test as run_test_action
from local_claude.orchestrator import executor, parser


def test_run_test_action_uses_cmd_override(tmp_path: Path) -> None:
    res = run_test_action.RunTestAction().execute({"cmd": "/bin/sh -c 'exit 0'", "cwd": str(tmp_path)})
    assert res.ok is True
    assert res.output["detected"] == "override"
    assert res.meta["attempts"] == 1


def test_run_test_action_failure_records_error(tmp_path: Path) -> None:
    res = run_test_action.RunTestAction().execute(
        {"cmd": "/bin/sh -c 'exit 3'", "cwd": str(tmp_path), "retries": 1}
    )
    assert res.ok is False
    assert res.error and "exit=3" in res.error
    assert res.meta["attempts"] == 2


def test_parser_recognizes_run_test_tag() -> None:
    text = '<run_test cmd="/bin/true" retries="0"/>'
    parsed = parser.extract(text)
    assert len(parsed) == 1
    assert parsed[0].name == "run_test"
    assert parsed[0].payload["cmd"] == "/bin/true"


def test_executor_dispatches_run_test(tmp_path: Path) -> None:
    text = '<run_test cmd="/bin/sh -c \'exit 0\'"/>'
    parsed = parser.extract(text)
    # cwd 는 주입되지 않으므로 payload 에 직접 추가.
    parsed[0].payload["cwd"] = str(tmp_path)
    executed = executor.dispatch_all(parsed, project_dir=tmp_path)
    assert len(executed) == 1
    assert executed[0].result.ok is True
