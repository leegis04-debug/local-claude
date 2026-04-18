"""ctx init → switch → get 전체 흐름."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ctx.cli import app


runner = CliRunner()


def test_init_creates_files_and_sets_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))

    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "current").read_text(encoding="utf-8").strip() == "home"
    assert (tmp_path / "contexts" / "home.toml").exists()
    assert (tmp_path / "contexts" / "office-daegyeom.toml").exists()
    assert (tmp_path / "contexts" / "offline.toml").exists()
    assert (tmp_path / "device.json").exists()
    # init 이 첫 switch 엔트리를 남김
    history_lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(history_lines) == 1
    assert json.loads(history_lines[0])["trigger"] == "init"


def test_switch_flow_and_history(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])

    r = runner.invoke(app, ["switch", "office-daegyeom"])
    assert r.exit_code == 0
    assert "home -> office-daegyeom" in r.output

    r = runner.invoke(app, ["current"])
    assert r.output.strip() == "office-daegyeom"

    r = runner.invoke(app, ["history", "--n", "5"])
    assert "office-daegyeom" in r.output
    assert "home" in r.output

    # 같은 context 재전환은 no-op
    r = runner.invoke(app, ["switch", "office-daegyeom"])
    assert "already active" in r.output


def test_switch_to_unknown_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])

    r = runner.invoke(app, ["switch", "doesnt-exist"])
    assert r.exit_code == 1
    assert "context not found" in r.output


def test_get_returns_gstar_namespace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])

    r = runner.invoke(app, ["get", "gstar.namespace"])
    assert r.exit_code == 0
    assert r.output.strip() == "personal"

    runner.invoke(app, ["switch", "office-daegyeom"])
    r = runner.invoke(app, ["get", "gstar.namespace"])
    assert r.output.strip() == "daegyeom"


def test_get_on_specific_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])

    r = runner.invoke(app, ["get", "display", "--context", "offline"])
    assert "오프라인" in r.output


def test_get_missing_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])

    r = runner.invoke(app, ["get", "nope.missing"])
    assert r.exit_code == 1


def test_list_marks_active(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["switch", "offline"])

    r = runner.invoke(app, ["list"])
    # offline 행이 * 표시
    lines = [line for line in r.output.splitlines() if "offline" in line]
    assert any(line.startswith("*") for line in lines)
