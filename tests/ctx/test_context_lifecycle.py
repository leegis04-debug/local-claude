"""ctx add/edit/rm + G namespace 자동 upsert 연동 테스트."""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from ctx.cli import app
from ctx.config import Paths
from ctx.context import current_context_name, list_context_names, load_context


runner = CliRunner()


def _init(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output


def test_add_creates_new_context(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, [
        "add", "office-newcorp",
        "--from", "offline",
        "--display", "🏢 NewCorp",
        "--color", "#d73a49",
    ])
    assert r.exit_code == 0, r.output
    assert "created: office-newcorp" in r.output
    data = load_context("office-newcorp", Paths.load())
    assert data["display"] == "🏢 NewCorp"
    assert data["color"] == "#d73a49"
    assert data["wifi"] == []  # 상속 안 됨
    assert data["gstar"]["namespace"] == "office-newcorp"


def test_add_respects_explicit_namespace(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, [
        "add", "office-x", "--from", "offline", "--namespace", "x-corp",
    ])
    assert r.exit_code == 0
    data = load_context("office-x", Paths.load())
    assert data["gstar"]["namespace"] == "x-corp"


def test_add_duplicate_fails(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    runner.invoke(app, ["add", "office-a", "--from", "offline"])
    r = runner.invoke(app, ["add", "office-a"])
    assert r.exit_code == 1
    assert "이미 존재" in r.output


def test_rm_rejects_active(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    # init 은 home 활성
    r = runner.invoke(app, ["rm", "home", "--yes"])
    assert r.exit_code == 1
    assert "활성 context" in r.output


def test_rm_requires_yes(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    # home 이 아닌 다른 context 시도
    r = runner.invoke(app, ["rm", "offline"])
    assert r.exit_code == 1
    assert "confirm" in r.output
    # 파일 여전히 존재
    assert (Paths.load().contexts_dir / "offline.toml").exists()


def test_rm_deletes_with_yes(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, ["rm", "offline", "--yes"])
    assert r.exit_code == 0
    assert "removed: offline" in r.output
    assert not (Paths.load().contexts_dir / "offline.toml").exists()
    # history 는 손대지 않음
    assert (tmp_path / "history.jsonl").exists()


def test_rm_unknown_fails(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, ["rm", "nope", "--yes"])
    assert r.exit_code == 1


def test_edit_invokes_editor(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    called = {}

    def fake_run(cmd, *args, **kwargs):
        called["cmd"] = cmd
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("ctx.cli.subprocess.run", fake_run)
    monkeypatch.setenv("EDITOR", "vim")
    r = runner.invoke(app, ["edit", "home"])
    assert r.exit_code == 0
    assert called["cmd"][0] == "vim"
    assert called["cmd"][-1].endswith("home.toml")


def test_wifi_add_and_remove(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, ["wifi", "add", "home", "HomeWifi"])
    assert r.exit_code == 0
    data = load_context("home", Paths.load())
    assert "HomeWifi" in (data.get("wifi") or [])

    r = runner.invoke(app, ["wifi", "add", "home", "HomeWifi"])
    assert "already present" in r.output

    r = runner.invoke(app, ["wifi", "remove", "home", "HomeWifi"])
    assert r.exit_code == 0
    data = load_context("home", Paths.load())
    assert "HomeWifi" not in (data.get("wifi") or [])


def test_wifi_bad_action(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    r = runner.invoke(app, ["wifi", "toggle", "home", "X"])
    assert r.exit_code == 1
