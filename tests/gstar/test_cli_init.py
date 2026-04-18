"""CLI init/status 스모크 테스트. GSTAR_HOME 격리."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from gstar.cli.main import app


def test_init_creates_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    runner = CliRunner()

    r = runner.invoke(app, ["init", "--dim", "8"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "state" / "g.duckdb").exists()
    assert (tmp_path / "state" / "emb.faiss").exists()


def test_status_reports_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["init", "--dim", "8"])

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    assert "nodes: 0" in r.output
    assert "goals: 0" in r.output


def test_status_without_init_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    runner = CliRunner()

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 1
